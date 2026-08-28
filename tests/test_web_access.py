'''Tests that every job endpoint is scoped to the user who submitted the job.'''

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import pdf_web.app as web_app
from pdf_web.models import Job, JobStatus, UploadedFile
from pdf_web.runner import PipelineRunner
from pdf_web.store import JobStore
from tests.web_factories import add_completed_result, make_job, write_job_artifacts

ALICE = "alice@courts.ca.gov"
BOB = "bob@courts.ca.gov"
SECRET = "s3cret"

# Every path that exposes a job's contents. If a new one is added without a
# matching owner check, it shows up here as a leak rather than in production.
JOB_ENDPOINTS = (
    "",
    "/log",
    "/download",
    "/files/000/pdf",
    "/files/000/before",
    "/files/000/after",
)


def headers(user: str, secret: str = SECRET) -> dict[str, str]:
    '''Build the headers an authenticating proxy would forward.'''
    return {
        "x-pdf-web-proxy-secret": secret,
        "x-forwarded-email": user,
    }


class AccessControlTests(unittest.TestCase):
    '''One user's documents must be unreachable to another.'''

    def setUp(self) -> None:
        '''Serve a single completed job owned by Alice, from a scratch tree.'''
        self.enterContext(mock.patch.dict(os.environ, {
            "PDF_WEB_PROXY_SECRET": SECRET,
        }))
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch.object(web_app, "JOBS_ROOT", self.jobs_root))

        self.store = JobStore()
        self.enterContext(mock.patch.object(web_app, "STORE", self.store))
        self.runner = mock.Mock(queue_depth=mock.Mock(return_value=0),
                                jobs_ahead=mock.Mock(return_value=None),
                                submit=mock.Mock(return_value=1))
        self.enterContext(mock.patch.object(web_app, "RUNNER", self.runner))

        self.client = TestClient(web_app.app)
        self.job = self._make_job("20260827-120000-aaaaaa", ALICE)

    def _make_job(self, job_id: str, owner: str) -> Job:
        '''
        Create a completed job on disk and register it.
        '''
        job = make_job(job_id=job_id, submitted_by=owner, status=JobStatus.COMPLETED)
        job.files[0] = UploadedFile("000", "Report.pdf", "Report.pdf", 5)
        add_completed_result(job, write_job_artifacts(job))
        self.store.add(job)
        return job

    def _url(self, endpoint: str) -> str:
        '''Build a job URL for one endpoint.'''
        return f"/api/jobs/{self.job.job_id}{endpoint}"

    def test_owner_reaches_every_endpoint(self) -> None:
        '''The scoping must not lock the owner out of their own job.'''
        for endpoint in JOB_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(self._url(endpoint), headers=headers(ALICE))
                self.assertEqual(response.status_code, 200)

    def test_other_user_is_refused_everywhere(self) -> None:
        '''Every endpoint that exposes job contents is owner-scoped.'''
        for endpoint in JOB_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(self._url(endpoint), headers=headers(BOB))
                self.assertEqual(response.status_code, 404)

    def test_refusal_is_indistinguishable_from_absence(self) -> None:
        '''404 rather than 403, so job identifiers cannot be probed.'''
        existing = self.client.get(self._url(""), headers=headers(BOB))
        missing = self.client.get(
            "/api/jobs/20260827-120000-ffffff", headers=headers(BOB)
        )
        self.assertEqual(existing.status_code, missing.status_code)
        self.assertEqual(existing.json(), missing.json())

    def test_other_user_cannot_delete(self) -> None:
        '''A stranger's delete must not destroy the owner's work.'''
        response = self.client.delete(self._url(""), headers=headers(BOB))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get(self._url(""), headers=headers(ALICE)).status_code, 200
        )
        self.assertTrue(self.job.log_path.is_file())

    def test_other_user_cannot_retry(self) -> None:
        '''Retry re-runs someone's documents, so it is owner-scoped too.'''
        response = self.client.post(
            self._url("/retry"), headers=headers(BOB), data={"skip_font_fix": "true"}
        )
        self.assertEqual(response.status_code, 404)
        self.runner.submit.assert_not_called()

    def test_job_list_is_scoped(self) -> None:
        '''Each user sees only their own jobs.'''
        self._make_job("20260827-130000-bbbbbb", BOB)

        alice_jobs = self.client.get("/api/jobs", headers=headers(ALICE)).json()["jobs"]
        bob_jobs = self.client.get("/api/jobs", headers=headers(BOB)).json()["jobs"]

        self.assertEqual([job["job_id"] for job in alice_jobs], [self.job.job_id])
        self.assertEqual([job["job_id"] for job in bob_jobs], ["20260827-130000-bbbbbb"])

    def test_event_stream_is_scoped(self) -> None:
        '''The live stream carries job output and is scoped like the rest.'''
        with self.client.stream(
            "GET", self._url("/events"), headers=headers(BOB)
        ) as response:
            self.assertEqual(response.status_code, 404)


class AuthenticationTests(unittest.TestCase):
    '''No identity means no access to any data endpoint.'''

    def setUp(self) -> None:
        '''Run the app in proxy mode with an empty store.'''
        self.enterContext(mock.patch.dict(os.environ, {
            "PDF_WEB_PROXY_SECRET": SECRET,
        }))
        self.enterContext(mock.patch.object(web_app, "STORE", JobStore()))
        self.client = TestClient(web_app.app)

    def test_data_endpoints_require_the_proxy_secret(self) -> None:
        '''A client that sets only the identity header is not authenticated.'''
        for path in ("/api/health", "/api/config-files", "/api/jobs"):
            with self.subTest(path=path):
                response = self.client.get(
                    path, headers={"x-forwarded-email": "boss@courts.ca.gov"}
                )
                self.assertEqual(response.status_code, 403)

    def test_wrong_secret_is_refused(self) -> None:
        '''A guessed secret does not authenticate.'''
        response = self.client.get("/api/jobs", headers=headers(ALICE, "wrong"))
        self.assertEqual(response.status_code, 403)

    def test_missing_identity_is_unauthenticated(self) -> None:
        '''A proxied request without an identity is rejected, not defaulted.'''
        response = self.client.get(
            "/api/jobs", headers={"x-pdf-web-proxy-secret": SECRET}
        )
        self.assertEqual(response.status_code, 401)

    def test_upload_requires_authentication(self) -> None:
        '''Submitting work is gated like reading it.'''
        response = self.client.post(
            "/api/jobs",
            files={"files": ("a.pdf", b"%PDF-1.7\n", "application/pdf")},
            data={"config_file": "default.json"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_index_is_served_without_data(self) -> None:
        '''The page shell carries no job data, so it may load before sign-in.'''
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("courts.ca.gov", response.text)


class OwnershipRecordingTests(unittest.TestCase):
    '''A new job belongs to whoever submitted it.'''

    def setUp(self) -> None:
        '''Run the app in proxy mode with a scratch jobs directory.'''
        self.enterContext(mock.patch.dict(os.environ, {
            "PDF_WEB_PROXY_SECRET": SECRET,
        }))
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch.object(web_app, "JOBS_ROOT", self.jobs_root))
        self.store = JobStore()
        self.enterContext(mock.patch.object(web_app, "STORE", self.store))
        self.enterContext(mock.patch.object(
            web_app, "RUNNER",
            mock.Mock(submit=mock.Mock(return_value=1))
        ))
        self.client = TestClient(web_app.app)

    def test_submission_records_the_caller(self) -> None:
        '''Ownership comes from the proxied identity, not from the request body.'''
        response = self.client.post(
            "/api/jobs",
            headers=headers(ALICE),
            files={"files": ("Report.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            data={"config_file": "default.json", "submitted_by": BOB},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["submitted_by"], ALICE)

        job = self.store.get(response.json()["job_id"])
        self.assertEqual(job.submitted_by, ALICE)

    def test_submitted_job_is_invisible_to_others(self) -> None:
        '''A freshly created job is private immediately, not once it finishes.'''
        created = self.client.post(
            "/api/jobs",
            headers=headers(ALICE),
            files={"files": ("Report.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            data={"config_file": "default.json"},
        ).json()
        response = self.client.get(
            f"/api/jobs/{created['job_id']}", headers=headers(BOB)
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class QueueFairnessTests(unittest.TestCase):
    '''One user must not be able to monopolize the single worker.'''

    def setUp(self) -> None:
        '''Run the app in proxy mode with a scratch jobs directory.'''
        self.enterContext(mock.patch.dict(os.environ, {
            "PDF_WEB_PROXY_SECRET": SECRET,
        }))
        os.environ.pop("PDF_WEB_MAX_JOBS_PER_USER", None)
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch.object(web_app, "JOBS_ROOT", self.jobs_root))
        self.store = JobStore()
        self.enterContext(mock.patch.object(web_app, "STORE", self.store))
        self.enterContext(mock.patch.object(
            web_app, "RUNNER",
            mock.Mock(submit=mock.Mock(return_value=0),
                      jobs_ahead=mock.Mock(return_value=None))
        ))
        self.client = TestClient(web_app.app)

    def _submit(self, user: str):
        '''Submit one small PDF as the given user.'''
        return self.client.post(
            "/api/jobs",
            headers=headers(user),
            files={"files": ("Report.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            data={"config_file": "default.json"},
        )

    def test_second_concurrent_submission_is_refused(self) -> None:
        '''A user with work in flight cannot queue more behind it.'''
        self.assertEqual(self._submit(ALICE).status_code, 201)
        second = self._submit(ALICE)
        self.assertEqual(second.status_code, 409)
        self.assertIn("queued or running", second.json()["detail"])

    def test_other_users_are_unaffected(self) -> None:
        '''The cap is per user, not a global lock.'''
        self.assertEqual(self._submit(ALICE).status_code, 201)
        self.assertEqual(self._submit(BOB).status_code, 201)

    def test_finished_jobs_free_the_slot(self) -> None:
        '''The cap counts work in flight, not work in history.'''
        first = self._submit(ALICE)
        self.store.get(first.json()["job_id"]).status = JobStatus.COMPLETED
        self.assertEqual(self._submit(ALICE).status_code, 201)

    def test_limit_is_configurable(self) -> None:
        '''A busier deployment can allow more per user.'''
        os.environ["PDF_WEB_MAX_JOBS_PER_USER"] = "2"
        self.assertEqual(self._submit(ALICE).status_code, 201)
        self.assertEqual(self._submit(ALICE).status_code, 201)
        self.assertEqual(self._submit(ALICE).status_code, 409)


class CancellationTests(unittest.TestCase):
    '''A mistaken job must be stoppable without restarting the server.'''

    def setUp(self) -> None:
        '''Run the app in proxy mode with a real runner that never starts.'''
        self.enterContext(mock.patch.dict(os.environ, {
            "PDF_WEB_PROXY_SECRET": SECRET,
        }))
        self.jobs_root = Path(self.enterContext(
            tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        ))
        self.enterContext(mock.patch("pdf_web.models.JOBS_ROOT", self.jobs_root))
        self.enterContext(mock.patch.object(web_app, "JOBS_ROOT", self.jobs_root))
        self.store = JobStore()
        self.enterContext(mock.patch.object(web_app, "STORE", self.store))
        self.runner = PipelineRunner(self.store)
        self.enterContext(mock.patch.object(web_app, "RUNNER", self.runner))
        self.client = TestClient(web_app.app)

    def _queued_job(self, job_id: str, owner: str) -> Job:
        '''Register a queued job and put it in the runner's queue.'''
        job = make_job(job_id=job_id, submitted_by=owner, status=JobStatus.QUEUED)
        job.web_path.mkdir(parents=True, exist_ok=True)
        self.store.add(job)
        self.runner.submit(job_id)
        return job

    def test_owner_can_cancel_a_queued_job(self) -> None:
        '''Cancelling reaches a terminal state without the worker running.'''
        job = self._queued_job("20260827-120000-aaaaaa", ALICE)

        response = self.client.post(
            f"/api/jobs/{job.job_id}/cancel", headers=headers(ALICE)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertTrue(job.is_terminal())

    def test_cancelling_frees_the_users_slot(self) -> None:
        '''The whole point: the submitter is not locked out by their own mistake.'''
        job = self._queued_job("20260827-120000-aaaaaa", ALICE)
        self.client.post(f"/api/jobs/{job.job_id}/cancel", headers=headers(ALICE))

        response = self.client.post(
            "/api/jobs",
            headers=headers(ALICE),
            files={"files": ("Report.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            data={"config_file": "default.json"},
        )
        self.assertEqual(response.status_code, 201)

    def test_cancelling_removes_it_from_the_queue(self) -> None:
        '''A cancelled job must not still be occupying the line.'''
        first = self._queued_job("20260827-120000-aaaaaa", ALICE)
        second = self._queued_job("20260827-120001-bbbbbb", BOB)
        self.assertEqual(self.runner.jobs_ahead(second.job_id), 1)

        self.client.post(f"/api/jobs/{first.job_id}/cancel", headers=headers(ALICE))

        self.assertEqual(self.runner.jobs_ahead(second.job_id), 0)
        self.assertNotIn(first.job_id, self.runner.pending_job_ids())

    def test_other_users_cannot_cancel(self) -> None:
        '''Cancelling destroys work, so it is owner-scoped like everything else.'''
        job = self._queued_job("20260827-120000-aaaaaa", ALICE)

        response = self.client.post(
            f"/api/jobs/{job.job_id}/cancel", headers=headers(BOB)
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(job.status, JobStatus.QUEUED)

    def test_finished_jobs_cannot_be_cancelled(self) -> None:
        '''A completed job is not stoppable, and says so.'''
        job = make_job(
            job_id="20260827-120002-cccccc", submitted_by=ALICE,
            status=JobStatus.COMPLETED
        )
        self.store.add(job)

        response = self.client.post(
            f"/api/jobs/{job.job_id}/cancel", headers=headers(ALICE)
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(job.status, JobStatus.COMPLETED)

    def test_cancelled_job_can_then_be_deleted(self) -> None:
        '''Cancellation is terminal, so the normal cleanup path applies.'''
        job = self._queued_job("20260827-120000-aaaaaa", ALICE)
        self.client.post(f"/api/jobs/{job.job_id}/cancel", headers=headers(ALICE))

        response = self.client.delete(
            f"/api/jobs/{job.job_id}", headers=headers(ALICE)
        )
        self.assertEqual(response.status_code, 200)


class LivenessTests(unittest.TestCase):
    '''Supervisors and load balancers must be able to probe without credentials.'''

    def setUp(self) -> None:
        '''Run the app in proxy mode so every data endpoint needs a secret.'''
        self.enterContext(mock.patch.dict(os.environ, {
            "PDF_WEB_PROXY_SECRET": SECRET,
        }))
        self.enterContext(mock.patch.object(web_app, "STORE", JobStore()))
        self.runner = mock.Mock(is_running=mock.Mock(return_value=True))
        self.enterContext(mock.patch.object(web_app, "RUNNER", self.runner))
        self.client = TestClient(web_app.app)

    def test_probe_needs_no_credentials(self) -> None:
        '''An unauthenticated probe is the entire purpose of this endpoint.'''
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_reports_degraded_when_the_worker_is_gone(self) -> None:
        '''A live process with a dead worker accepts jobs it will never run.'''
        self.runner.is_running.return_value = False
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["worker"], "stopped")

    def test_probe_reveals_nothing_sensitive(self) -> None:
        '''Licence, tooling, and identity configuration stay behind auth.'''
        body = self.client.get("/healthz").text.lower()
        for leak in ("pdfix", "callas", "docker", "java", "forwarded", "secret"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)

    def test_detailed_health_still_requires_authentication(self) -> None:
        '''The informative endpoint is unchanged.'''
        self.assertEqual(self.client.get("/api/health").status_code, 403)
