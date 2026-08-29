'''Deployment-specific runtime contracts for the Azure VM image.'''

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from python_on_whales.exceptions import DockerException

import pdf_web.app as web_app
import pdf_web.config as web_config
import pdf_web.environment as web_environment
from pdf_api.capabilities import Capabilities, _callas_license_status
from pdf_api.scratch import scratch_workspace
from pdf_remediation.utilities.callas import Callas


class ConfiguredPathTests(unittest.TestCase):
    '''Azure mounts must replace repository-local runtime paths.'''

    def test_configured_jobs_root_is_absolute(self) -> None:
        '''A deployment path is expanded and resolved once at startup.'''
        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "jobs"
            with mock.patch.dict(os.environ, {"PDF_WEB_JOBS_ROOT": str(configured)}):
                self.assertEqual(
                    web_config._read_path_env(  # pylint: disable=protected-access
                        "PDF_WEB_JOBS_ROOT", Path("unused")
                    ),
                    configured.resolve(),
                )

    def test_scratch_workspace_uses_configured_parent(self) -> None:
        '''Docker bind sources stay below the host/container shared path.'''
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"PDF_SCRATCH_ROOT": directory}):
                with scratch_workspace() as scratch:
                    root = scratch.root
                    self.assertEqual(root.parent, Path(directory).resolve())
                    self.assertTrue(scratch.workspace.is_relative_to(root))
                self.assertFalse(root.exists())


class ManagedSecretTests(unittest.TestCase):
    '''Production credentials arrive from Key Vault-backed environment values.'''

    def test_callas_capability_accepts_environment_credentials(self) -> None:
        '''The ignored developer env file is not required in Azure.'''
        with mock.patch.dict(os.environ, {
            "ENV_CALLAS_LICENSE": "license",
            "ENV_CALLAS_SECRET": "secret",
        }, clear=False):
            self.assertEqual(
                _callas_license_status(),  # pylint: disable=protected-access
                (True, "configured in environment"),
            )

    @mock.patch("pdf_remediation.utilities.callas.docker.run")
    @mock.patch("pdf_remediation.utilities.callas.ensure_docker_desktop_running")
    def test_callas_passes_environment_to_child_container(
            self, _ensure: mock.Mock, run: mock.Mock) -> None:
        '''Secrets are passed directly rather than written to resources/font/.env.'''
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            input_pdf = workspace / "input.pdf"
            output_pdf = workspace / "output.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            with mock.patch.dict(os.environ, {
                "ENV_CALLAS_LICENSE": "license",
                "ENV_CALLAS_SECRET": "secret",
                "PDFIX_LICENSE_NAME": "pdfix-name",
                "PDFIX_LICENSE_KEY": "pdfix-key",
            }, clear=False):
                Callas.font_fix(input_pdf, output_pdf, workspace)

        command = run.call_args.args[1]
        self.assertEqual(command, [
            "fix",
            "--name", "pdfix-name",
            "--key", "pdfix-key",
            "-i", "input.pdf",
            "-o", "output.pdf",
        ])
        options = run.call_args.kwargs
        self.assertEqual(options["envs"], {
            "ENV_CALLAS_LICENSE": "license",
            "ENV_CALLAS_SECRET": "secret",
        })
        self.assertNotIn("env_files", options)

    @mock.patch("pdf_remediation.utilities.callas.docker.run")
    def test_callas_requires_pdfix_credentials_for_current_worker(
            self, run: mock.Mock) -> None:
        '''Callas v1.0.12 initializes PDFix before invoking pdfaPilot.'''
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            input_pdf = workspace / "input.pdf"
            output_pdf = workspace / "output.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            with mock.patch.dict(os.environ, {
                "ENV_CALLAS_LICENSE": "license",
                "ENV_CALLAS_SECRET": "secret",
                "PDFIX_LICENSE_NAME": "",
                "PDFIX_LICENSE_KEY": "",
            }, clear=False):
                with self.assertRaisesRegex(RuntimeError, "PDFIX_LICENSE_NAME"):
                    Callas.font_fix(input_pdf, output_pdf, workspace)

        run.assert_not_called()

    @mock.patch("pdf_remediation.utilities.callas.ensure_docker_desktop_running")
    @mock.patch("pdf_remediation.utilities.callas.docker.run")
    def test_callas_failure_does_not_expose_pdfix_key(
            self, run: mock.Mock, _ensure: mock.Mock) -> None:
        '''The vendor CLI requires a key argument, which must stay out of errors.'''
        run.side_effect = DockerException(
            ["docker", "run", "--key", "sensitive-pdfix-key"], 23
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            input_pdf = workspace / "input.pdf"
            output_pdf = workspace / "output.pdf"
            input_pdf.write_bytes(b"%PDF-1.7\n")
            with mock.patch.dict(os.environ, {
                "ENV_CALLAS_LICENSE": "license",
                "ENV_CALLAS_SECRET": "secret",
                "PDFIX_LICENSE_NAME": "pdfix-name",
                "PDFIX_LICENSE_KEY": "sensitive-pdfix-key",
            }, clear=False):
                with self.assertRaises(RuntimeError) as raised:
                    Callas.font_fix(input_pdf, output_pdf, workspace)

        self.assertNotIn("sensitive-pdfix-key", str(raised.exception))


class ReadinessTests(unittest.TestCase):
    '''Readiness is strict internally and deliberately terse publicly.'''

    def test_all_production_dependencies_can_be_ready(self) -> None:
        '''The aggregate passes only when tooling, secrets, images, and disks pass.'''
        capabilities = Capabilities(
            java=True,
            verapdf_jar=True,
            docker=True,
            pdfix_licence=True,
            callas_licence=True,
            detail={
                "java": "ok",
                "verapdf_jar": "ok",
                "docker": "ok",
                "pdfix_licence": "configured",
                "callas_licence": "configured",
            },
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.multiple(
                web_environment,
                cached_probe=mock.Mock(return_value=capabilities),
                CONFIG_DIR=Path(directory),
                ALLOWED_CONFIG_FILES=(),
                JOBS_ROOT=Path(directory) / "jobs",
                SCRATCH_ROOT=Path(directory) / "scratch",
                _docker_image_available=mock.Mock(return_value=(True, "present")),
        ), mock.patch("pdf_web.environment.shutil.disk_usage") as disk_usage:
            disk_usage.return_value.free = 10 * 1024 ** 3
            result = web_environment.collect_readiness()
        self.assertTrue(result["ready"])
        self.assertEqual(result["blocking"], [])

    def test_public_endpoint_hides_blocking_dependency_names(self) -> None:
        '''Unauthenticated callers learn only ready/not-ready and the version.'''
        with mock.patch.object(
                web_app, "collect_readiness",
                return_value={"ready": False, "blocking": ["PDFix license"]},
        ):
            response = TestClient(web_app.app).get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(set(response.json()), {"status", "version"})
        self.assertNotIn("pdfix", response.text.lower())


if __name__ == "__main__":
    unittest.main()
