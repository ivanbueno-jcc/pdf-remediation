'''
Command line entry point for the PDF remediation web application.
'''

from __future__ import annotations

import argparse
import sys

import uvicorn

from . import APP_NAME, APP_VERSION
from .identity import (
    describe_mode,
    header_diagnostic_enabled,
    identity_header_name,
    proxy_mode_enabled,
)


def build_parser() -> argparse.ArgumentParser:
    '''
    Build the CLI parser.
    '''
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} {APP_VERSION}: run the remediation pipeline from a browser."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: %(default)s)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: %(default)s)."
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "Permit binding a non-loopback interface. Requires PDF_WEB_PROXY_SECRET, "
            "because the app authenticates by trusting a proxy-supplied header."
        )
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on source changes (development only)."
    )
    return parser


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def check_bind_safety(host: str, allow_remote: bool) -> str | None:
    """
    Return an error message when this bind would expose unauthenticated access.

    Identity is taken from a header set by an authenticating proxy. That header
    is only meaningful if clients cannot set it themselves, which is what the
    shared secret establishes. Binding beyond loopback without it would let any
    caller name themselves any user, so it is refused rather than warned about.
    """
    if host in LOOPBACK_HOSTS:
        return None

    if not allow_remote:
        return (
            f"Refusing to bind {host}: pass --allow-remote to serve a "
            "non-loopback interface."
        )

    if not proxy_mode_enabled():
        return (
            f"Refusing to bind {host}: no proxy trust is configured.\n"
            "\n"
            "This application does not authenticate users itself. It trusts an\n"
            f"identity forwarded in the {identity_header_name()} header by an\n"
            "authenticating proxy. Without proof that a request came through\n"
            "that proxy, anyone able to reach this port could set the header\n"
            "and act as any user.\n"
            "\n"
            "Configure at least one proof:\n"
            "  PDF_WEB_PROXY_SECRET     a secret header the proxy sets and\n"
            "                           clients cannot (oauth2-proxy, PingAccess)\n"
            "  PDF_WEB_TRUSTED_PROXY_IPS  source addresses allowed to assert an\n"
            "                           identity (Entra Application Proxy\n"
            "                           connector hosts)\n"
            "\n"
            "Or bind 127.0.0.1 to run in single-user mode."
        )

    return None


def describe_startup(host: str, port: int) -> None:
    """
    Report which authentication mode the server is starting in.
    """
    if proxy_mode_enabled():
        mode = describe_mode()
        print(
            f"{APP_NAME}: multi-user mode on {host}:{port}; "
            f"identity from {', '.join(mode['identity_headers'])}; "
            f"trust via {', '.join(mode['trust'])}."
        )
        if header_diagnostic_enabled():
            print(
                f"{APP_NAME}: WARNING - header diagnostic is enabled at "
                "/api/proxy-headers and is reachable without authentication. "
                "Unset PDF_WEB_HEADER_DIAGNOSTIC when finished."
            )
        return
    print(
        f"{APP_NAME}: single-user mode on {host}:{port}; "
        "loopback only, no authentication."
    )


def main(argv: list[str] | None = None) -> int:
    '''
    Start the web application with a single worker process.

    Job state lives in this process, so the pipeline must never be spread
    across multiple uvicorn workers.
    '''
    args = build_parser().parse_args(argv)

    error = check_bind_safety(args.host, args.allow_remote)
    if error is not None:
        print(error, file=sys.stderr)
        return 2

    describe_startup(args.host, args.port)

    uvicorn.run(
        "pdf_web.app:app",
        host=args.host,
        port=args.port,
        workers=1,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
