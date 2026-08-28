'''Tests for the trust boundary between the app and its authenticating proxy.'''

from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi import HTTPException

from pdf_web.__main__ import check_bind_safety
from pdf_web.identity import (
    DEFAULT_DEV_USER,
    assert_proxy_secret,
    describe_mode,
    diagnose_request,
    header_diagnostic_enabled,
    identity_header_names,
    legacy_job_owner,
    normalize_user,
    proxy_mode_enabled,
    redact_headers,
    resolve_user,
)

AUTH_ENVIRONMENT = (
    "PDF_WEB_PROXY_SECRET",
    "PDF_WEB_TRUSTED_PROXY_IPS",
    "PDF_WEB_HEADER_DIAGNOSTIC",
    "PDF_WEB_IDENTITY_HEADER",
    "PDF_WEB_PROXY_SECRET_HEADER",
    "PDF_WEB_DEV_USER",
    "PDF_WEB_LEGACY_JOB_OWNER",
)


class FakeRequest:  # pylint: disable=too-few-public-methods
    '''A request carrying only the headers and peer address identity reads.'''

    def __init__(self, headers: dict[str, str] | None = None,
                 host: str | None = "127.0.0.1") -> None:
        '''Build a request with case-insensitive headers.'''
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.client = mock.Mock(host=host) if host is not None else None


class IdentityTestCase(unittest.TestCase):
    '''Base case that isolates the authentication environment.'''

    def setUp(self) -> None:
        '''Clear every authentication variable for each test.'''
        self.enterContext(mock.patch.dict(os.environ, {}, clear=False))
        for name in AUTH_ENVIRONMENT:
            os.environ.pop(name, None)

    @staticmethod
    def enable_proxy(secret: str = "s3cret") -> None:
        '''Turn on proxy mode with a known secret.'''
        os.environ["PDF_WEB_PROXY_SECRET"] = secret


class ProxySecretTests(IdentityTestCase):
    '''The shared secret is what makes a forwarded identity believable.'''

    def test_identity_header_alone_is_refused(self) -> None:
        '''A client setting the identity header directly must not be trusted.

        This is the whole threat model: without the secret, anyone able to
        reach the port could name themselves any user.
        '''
        self.enable_proxy()
        request = FakeRequest({"x-forwarded-email": "boss@example.com"})
        with self.assertRaises(HTTPException) as caught:
            resolve_user(request)
        self.assertEqual(caught.exception.status_code, 403)

    def test_wrong_secret_is_refused(self) -> None:
        '''A guessed secret does not authenticate.'''
        self.enable_proxy()
        request = FakeRequest({
            "x-pdf-web-proxy-secret": "wrong",
            "x-forwarded-email": "a@example.com",
        })
        with self.assertRaises(HTTPException) as caught:
            resolve_user(request)
        self.assertEqual(caught.exception.status_code, 403)

    def test_secret_prefix_is_refused(self) -> None:
        '''Comparison is not a prefix match.'''
        self.enable_proxy("s3cret")
        request = FakeRequest({
            "x-pdf-web-proxy-secret": "s3c",
            "x-forwarded-email": "a@example.com",
        })
        with self.assertRaises(HTTPException):
            assert_proxy_secret(request)

    def test_correct_secret_and_identity_resolves(self) -> None:
        '''A properly proxied request yields the forwarded user.'''
        self.enable_proxy()
        request = FakeRequest({
            "x-pdf-web-proxy-secret": "s3cret",
            "x-forwarded-email": "Alice@Courts.CA.gov",
        })
        self.assertEqual(resolve_user(request), "alice@courts.ca.gov")

    def test_missing_identity_is_unauthenticated(self) -> None:
        '''A proxied request with no usable identity is rejected, not defaulted.'''
        self.enable_proxy()
        request = FakeRequest({"x-pdf-web-proxy-secret": "s3cret"})
        with self.assertRaises(HTTPException) as caught:
            resolve_user(request)
        self.assertEqual(caught.exception.status_code, 401)

    def test_header_names_are_configurable(self) -> None:
        '''Different proxies forward different headers.'''
        self.enable_proxy()
        os.environ["PDF_WEB_IDENTITY_HEADER"] = "X-Auth-User"
        os.environ["PDF_WEB_PROXY_SECRET_HEADER"] = "X-Gateway-Token"
        request = FakeRequest({
            "x-gateway-token": "s3cret",
            "x-auth-user": "carol@example.com",
        })
        self.assertEqual(resolve_user(request), "carol@example.com")


class SingleUserModeTests(IdentityTestCase):
    '''Without a proxy secret the app serves one local operator.'''

    def test_loopback_resolves_to_the_dev_user(self) -> None:
        '''Local use needs no proxy and no configuration.'''
        self.assertFalse(proxy_mode_enabled())
        self.assertEqual(resolve_user(FakeRequest()), DEFAULT_DEV_USER)

    def test_remote_client_is_refused(self) -> None:
        '''Single-user mode never serves a remote caller, whatever the bind.'''
        request = FakeRequest(host="10.0.0.5")
        with self.assertRaises(HTTPException) as caught:
            resolve_user(request)
        self.assertEqual(caught.exception.status_code, 403)

    def test_client_without_an_address_is_refused(self) -> None:
        '''An unidentifiable peer is not assumed to be local.'''
        with self.assertRaises(HTTPException):
            resolve_user(FakeRequest(host=None))

    def test_identity_headers_are_ignored(self) -> None:
        '''Header spoofing cannot escalate in single-user mode either.'''
        request = FakeRequest({"x-forwarded-email": "boss@example.com"})
        self.assertEqual(resolve_user(request), DEFAULT_DEV_USER)


class NormalizeUserTests(IdentityTestCase):
    '''Identities are stored, compared, and logged, so they are validated.'''

    def test_normalizes_case_and_whitespace(self) -> None:
        '''One person must not become two owners through formatting.'''
        self.assertEqual(normalize_user("  Alice@Example.COM "), "alice@example.com")

    def test_rejects_unusable_values(self) -> None:
        '''Empty, oversized, and structurally odd identities are refused.'''
        for value in ("", "   ", None, "a" * 300, "../etc/passwd",
                      "a b@example.com", "<script>", "@example.com"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_user(value))

    def test_accepts_plain_usernames(self) -> None:
        '''Not every provider forwards an email address.'''
        self.assertEqual(normalize_user("jsmith"), "jsmith")


class LegacyJobOwnerTests(IdentityTestCase):
    '''Job directories predating ownership must not leak to the first caller.'''

    def test_unowned_jobs_are_unreachable_in_proxy_mode(self) -> None:
        '''Inventing an owner would hand one user another's documents.'''
        self.enable_proxy()
        self.assertIsNone(legacy_job_owner())

    def test_single_user_mode_keeps_its_own_history(self) -> None:
        '''With one possible owner there is nothing to leak.'''
        self.assertEqual(legacy_job_owner(), DEFAULT_DEV_USER)

    def test_explicit_owner_is_honoured(self) -> None:
        '''An operator can adopt existing jobs deliberately.'''
        self.enable_proxy()
        os.environ["PDF_WEB_LEGACY_JOB_OWNER"] = "Admin@Example.com"
        self.assertEqual(legacy_job_owner(), "admin@example.com")


class BindSafetyTests(IdentityTestCase):
    '''Binding beyond loopback without a proxy would expose everything.'''

    def test_loopback_needs_nothing(self) -> None:
        '''Local operation is always allowed.'''
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                self.assertIsNone(check_bind_safety(host, False))

    def test_remote_bind_requires_the_flag(self) -> None:
        '''Exposing the app is never accidental.'''
        self.assertIsNotNone(check_bind_safety("0.0.0.0", False))

    def test_remote_bind_requires_a_proxy_secret(self) -> None:
        '''The flag alone would serve unauthenticated, spoofable access.'''
        message = check_bind_safety("0.0.0.0", True)
        self.assertIsNotNone(message)
        self.assertIn("PDF_WEB_PROXY_SECRET", message)

    def test_remote_bind_allowed_once_configured(self) -> None:
        '''With the secret set, the identity header can be believed.'''
        self.enable_proxy()
        self.assertIsNone(check_bind_safety("0.0.0.0", True))

    def test_mode_description_tracks_configuration(self) -> None:
        '''Startup and the browser report the same mode.'''
        self.assertFalse(describe_mode()["multi_user"])
        self.enable_proxy()
        self.assertTrue(describe_mode()["multi_user"])


class SourceAllowlistTests(IdentityTestCase):
    """Entra Application Proxy cannot inject a secret header, so origin is the proof."""

    def test_allowlist_alone_enables_multi_user(self) -> None:
        """A source allowlist is sufficient proof of origin without a secret."""
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "10.20.0.0/24"
        self.assertTrue(proxy_mode_enabled())

    def test_connector_address_is_accepted(self) -> None:
        """A request from the connector carries a believable identity."""
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "10.20.0.0/24"
        request = FakeRequest({"x-forwarded-email": "a@example.com"}, host="10.20.0.7")
        self.assertEqual(resolve_user(request), "a@example.com")

    def test_other_addresses_are_refused(self) -> None:
        """Anyone bypassing the connector cannot assert an identity."""
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "10.20.0.0/24"
        request = FakeRequest({"x-forwarded-email": "boss@example.com"}, host="10.99.0.1")
        with self.assertRaises(HTTPException) as caught:
            resolve_user(request)
        self.assertEqual(caught.exception.status_code, 403)

    def test_both_proofs_are_required_when_both_configured(self) -> None:
        """Configuring a secret as well as an allowlist demands both."""
        self.enable_proxy()
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "10.20.0.0/24"
        headers = {"x-forwarded-email": "a@example.com"}

        from_wrong_source = FakeRequest(
            {**headers, "x-pdf-web-proxy-secret": "s3cret"}, host="10.99.0.1"
        )
        with self.assertRaises(HTTPException):
            resolve_user(from_wrong_source)

        without_secret = FakeRequest(headers, host="10.20.0.7")
        with self.assertRaises(HTTPException):
            resolve_user(without_secret)

        valid = FakeRequest(
            {**headers, "x-pdf-web-proxy-secret": "s3cret"}, host="10.20.0.7"
        )
        self.assertEqual(resolve_user(valid), "a@example.com")

    def test_malformed_allowlist_entries_are_skipped_not_widening(self) -> None:
        """A typo must neither widen trust nor discard the valid entries.

        Discarding them would disable proxy mode altogether and silently fall
        back to single-user behaviour, so both halves are asserted.
        """
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "not-an-ip, 10.20.0.0/24"
        headers = {"x-forwarded-email": "a@example.com"}

        self.assertTrue(proxy_mode_enabled())
        self.assertEqual(
            resolve_user(FakeRequest(headers, host="10.20.0.7")),
            "a@example.com"
        )
        with self.assertRaises(HTTPException):
            resolve_user(FakeRequest(headers, host="10.99.0.1"))


class IdentityHeaderListTests(IdentityTestCase):
    """Proxies disagree on which header carries the signed-in user."""

    def test_defaults_to_one_header(self) -> None:
        """Unconfigured deployments keep the previous single-header behaviour."""
        self.assertEqual(identity_header_names(), ("x-forwarded-email",))

    def test_accepts_a_priority_list(self) -> None:
        """Several candidate headers can be tried in order."""
        os.environ["PDF_WEB_IDENTITY_HEADER"] = (
            "X-Ms-Client-Principal-Name, X-Forwarded-Email"
        )
        self.assertEqual(
            identity_header_names(),
            ("x-ms-client-principal-name", "x-forwarded-email")
        )

    def test_first_present_header_wins(self) -> None:
        """The fallback is used only when the preferred header is absent."""
        self.enable_proxy()
        os.environ["PDF_WEB_IDENTITY_HEADER"] = (
            "x-ms-client-principal-name,x-forwarded-email"
        )
        secret = {"x-pdf-web-proxy-secret": "s3cret"}

        both = FakeRequest({
            **secret,
            "x-ms-client-principal-name": "primary@example.com",
            "x-forwarded-email": "fallback@example.com",
        })
        self.assertEqual(resolve_user(both), "primary@example.com")

        fallback_only = FakeRequest({**secret, "x-forwarded-email": "fallback@example.com"})
        self.assertEqual(resolve_user(fallback_only), "fallback@example.com")


class HeaderDiagnosticTests(IdentityTestCase):
    """The diagnostic exists to explain a failing deployment without leaking."""

    def test_disabled_by_default(self) -> None:
        """It must be switched on deliberately."""
        self.assertFalse(header_diagnostic_enabled())

    def test_redacts_the_configured_secret_header_by_name(self) -> None:
        """The secret header is redacted because it is the secret header.

        The default name contains "secret", which the substring rule would
        catch anyway, so this uses a name no substring rule matches.
        """
        self.enable_proxy()
        os.environ["PDF_WEB_PROXY_SECRET_HEADER"] = "X-Gateway-Proof"
        redacted = redact_headers({
            "X-Gateway-Proof": "s3cret",
            "X-Forwarded-Email": "a@example.com",
        })
        self.assertTrue(redacted["x-gateway-proof"].startswith("<redacted"))
        self.assertEqual(redacted["x-forwarded-email"], "a@example.com")

    def test_redacts_credentials(self) -> None:
        """Header names are shown; credential values never are."""
        self.enable_proxy()
        redacted = redact_headers({
            "X-Pdf-Web-Proxy-Secret": "s3cret",
            "Authorization": "Bearer abc.def",
            "Cookie": "session=xyz",
            "X-Access-Token": "abc",
            "X-Forwarded-Email": "a@example.com",
            "User-Agent": "curl/8",
        })
        for name in ("x-pdf-web-proxy-secret", "authorization", "cookie", "x-access-token"):
            with self.subTest(header=name):
                self.assertIn(name, redacted)
                self.assertTrue(redacted[name].startswith("<redacted"))
        self.assertEqual(redacted["x-forwarded-email"], "a@example.com")
        self.assertEqual(redacted["user-agent"], "curl/8")

    def test_reports_why_a_request_fails(self) -> None:
        """A rejected request is explained rather than merely refused."""
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "10.20.0.0/24"
        report = diagnose_request(
            FakeRequest({"x-forwarded-email": "a@example.com"}, host="10.99.0.1")
        )
        self.assertFalse(report["source_trusted"])
        self.assertFalse(report["would_authenticate"])
        self.assertEqual(report["resolved_user"], "a@example.com")

    def test_reports_a_proxy_forwarding_no_identity(self) -> None:
        """Passthrough proxies forward no user, which must be visible."""
        os.environ["PDF_WEB_TRUSTED_PROXY_IPS"] = "10.20.0.0/24"
        report = diagnose_request(FakeRequest({"user-agent": "curl/8"}, host="10.20.0.7"))
        self.assertTrue(report["source_trusted"])
        self.assertIsNone(report["resolved_user"])
        self.assertFalse(report["would_authenticate"])
        self.assertEqual(report["identity_headers_found"], {})


if __name__ == "__main__":
    unittest.main()
