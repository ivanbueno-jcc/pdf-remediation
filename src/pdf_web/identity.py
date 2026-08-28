'''
Resolve the authenticated user from a trusting reverse proxy.

Authentication is delegated to an authenticating proxy (oauth2-proxy,
Cloudflare Access, Entra Application Proxy) which terminates TLS and SSO and
forwards a verified identity header. This module's only job is to decide
whether that header can be believed.

That decision is the entire security model. A forwarded identity header is
trustworthy only when the request provably came from the proxy, so proxy mode
requires proof of origin. Without it, anyone able to reach the application
directly could name themselves anyone.

Two proofs are supported, and either or both may be configured:

- A shared secret header the proxy sets and clients cannot. Use this where the
  proxy can inject arbitrary headers, such as oauth2-proxy.
- A source address allowlist. Use this where the proxy cannot inject a custom
  header, such as Microsoft Entra Application Proxy, whose connector is the
  only host able to reach the application.
'''

from __future__ import annotations

import ipaddress
import os
import re
import secrets

from fastapi import HTTPException, Request

DEFAULT_IDENTITY_HEADER = "x-forwarded-email"
DEFAULT_SECRET_HEADER = "x-pdf-web-proxy-secret"
DEFAULT_DEV_USER = "local"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
MAX_USER_LENGTH = 254
USER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+-]*(@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)?$"
)


def identity_header_names() -> tuple[str, ...]:
    '''
    Return the headers that may carry the authenticated user, in priority order.

    Proxies disagree on this name, so a comma-separated list is accepted and
    the first header actually present wins.
    '''
    configured = os.getenv("PDF_WEB_IDENTITY_HEADER", "")
    names = tuple(
        name.strip().lower() for name in configured.split(",") if name.strip()
    )
    return names or (DEFAULT_IDENTITY_HEADER,)


def identity_header_name() -> str:
    '''
    Return the primary identity header, for messages and diagnostics.
    '''
    return identity_header_names()[0]


def trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    '''
    Return the source addresses permitted to assert an identity.
    '''
    configured = os.getenv("PDF_WEB_TRUSTED_PROXY_IPS", "")
    networks = []
    for entry in configured.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def secret_header_name() -> str:
    '''
    Return the header carrying the shared proxy secret.
    '''
    name = os.getenv("PDF_WEB_PROXY_SECRET_HEADER", "").strip()
    return (name or DEFAULT_SECRET_HEADER).lower()


def proxy_secret() -> str:
    '''
    Return the secret shared with the authenticating proxy.
    '''
    return os.getenv("PDF_WEB_PROXY_SECRET", "").strip()


def proxy_mode_enabled() -> bool:
    '''
    Return whether forwarded identity headers are trusted.
    '''
    return bool(proxy_secret()) or bool(trusted_proxy_networks())


def dev_user() -> str:
    '''
    Return the identity used for single-user loopback operation.
    '''
    return normalize_user(os.getenv("PDF_WEB_DEV_USER", "")) or DEFAULT_DEV_USER


def legacy_job_owner() -> str | None:
    '''
    Return the owner assigned to job directories that predate ownership.

    Unowned jobs are unreachable by default: inventing an owner would hand one
    user another's documents. In single-user mode the dev user is the only
    possible owner, so the jobs stay reachable there.
    '''
    configured = normalize_user(os.getenv("PDF_WEB_LEGACY_JOB_OWNER", ""))
    if configured:
        return configured
    return None if proxy_mode_enabled() else dev_user()


def normalize_user(raw_user: object) -> str | None:
    '''
    Normalize and validate an identity, returning None when unusable.
    '''
    text = str(raw_user or "").strip().lower()
    if not text or len(text) > MAX_USER_LENGTH:
        return None
    if not USER_PATTERN.match(text):
        return None
    return text


def assert_proxy_secret(request: Request) -> None:
    '''
    Reject any request that did not come through the authenticating proxy.
    '''
    expected = proxy_secret()
    presented = request.headers.get(secret_header_name(), "")
    if not secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail="This request did not come through the authenticating proxy."
        )


def assert_trusted_source(request: Request) -> None:
    '''
    Reject any request whose source address is not an approved proxy.
    '''
    networks = trusted_proxy_networks()
    if not networks:
        return

    client_host = request.client.host if request.client else None
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="This request did not come from an approved proxy."
        ) from None

    if not any(address in network for network in networks):
        raise HTTPException(
            status_code=403,
            detail="This request did not come from an approved proxy."
        )


def assert_loopback(request: Request) -> None:
    '''
    Confine single-user mode to the local machine.
    '''
    client_host = request.client.host if request.client else None
    if client_host not in LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=(
                "Remote access requires an authenticating proxy. "
                "Set PDF_WEB_PROXY_SECRET and forward an identity header."
            )
        )


def resolve_user(request: Request) -> str:
    '''
    Return the authenticated user for a request.
    '''
    if not proxy_mode_enabled():
        assert_loopback(request)
        return dev_user()

    if proxy_secret():
        assert_proxy_secret(request)
    assert_trusted_source(request)

    user = first_identity(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "The proxy did not forward a usable identity in any of: "
                + ", ".join(identity_header_names())
                + "."
            )
        )
    return user


def first_identity(request: Request) -> str | None:
    '''
    Return the user from the first identity header present on a request.
    '''
    for header_name in identity_header_names():
        user = normalize_user(request.headers.get(header_name))
        if user is not None:
            return user
    return None


REDACTED_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
})
REDACTED_SUBSTRINGS = ("secret", "token", "password", "key", "assertion")


def header_diagnostic_enabled() -> bool:
    '''
    Return whether the proxy header diagnostic is exposed.
    '''
    return os.getenv("PDF_WEB_HEADER_DIAGNOSTIC", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    '''
    Return headers with credential-bearing values replaced by a placeholder.

    Names are always shown: knowing which header carries the secret is what
    makes a misconfiguration diagnosable, while its value is never needed.
    '''
    secret_header = secret_header_name()
    redacted = {}
    for name, value in headers.items():
        lowered = name.lower()
        sensitive = (
            lowered == secret_header
            or lowered in REDACTED_HEADERS
            or any(fragment in lowered for fragment in REDACTED_SUBSTRINGS)
        )
        redacted[lowered] = f"<redacted, {len(value)} chars>" if sensitive else value
    return redacted


def diagnose_request(request: Request) -> dict[str, object]:
    '''
    Describe what a proxy actually forwarded, and whether it would authenticate.

    Deployments differ in which header carries the signed-in user, and some
    proxy modes forward no identity at all. This reports what arrived so the
    answer is observed rather than assumed.
    '''
    headers = redact_headers(dict(request.headers))
    candidates = {
        name: request.headers.get(name) for name in identity_header_names()
    }
    resolved = first_identity(request)

    source = request.client.host if request.client else None
    networks = trusted_proxy_networks()
    source_trusted = None
    if networks:
        try:
            source_trusted = any(
                ipaddress.ip_address(source) in network for network in networks
            )
        except ValueError:
            source_trusted = False

    return {
        "source_address": source,
        "source_trusted": source_trusted,
        "secret_header_present": bool(request.headers.get(secret_header_name())),
        "identity_headers_checked": list(identity_header_names()),
        "identity_headers_found": {
            name: value for name, value in candidates.items() if value
        },
        "resolved_user": resolved,
        "would_authenticate": _would_authenticate(request, resolved),
        "headers": headers,
    }


def _would_authenticate(request: Request, resolved: str | None) -> bool:
    '''
    Report whether this exact request would be accepted.
    '''
    try:
        resolve_user(request)
    except HTTPException:
        return False
    return resolved is not None or not proxy_mode_enabled()


def describe_mode() -> dict[str, object]:
    '''
    Describe the active authentication mode for the browser and for startup logs.
    '''
    if proxy_mode_enabled():
        return {
            "mode": "proxy",
            "identity_header": identity_header_name(),
            "identity_headers": list(identity_header_names()),
            "trust": [
                name for name, active in (
                    ("shared secret", bool(proxy_secret())),
                    ("source allowlist", bool(trusted_proxy_networks())),
                ) if active
            ],
            "multi_user": True,
        }
    return {
        "mode": "single-user",
        "identity_header": None,
        "identity_headers": [],
        "trust": ["loopback only"],
        "multi_user": False,
    }
