'''
Command line entry point for the PDF remediation web application.
'''

from __future__ import annotations

import argparse
import sys

import uvicorn

from . import APP_NAME, APP_VERSION


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
        help="Permit binding a non-loopback interface. The app has no authentication."
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on source changes (development only)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    '''
    Start the web application with a single worker process.

    Job state lives in this process, so the pipeline must never be spread
    across multiple uvicorn workers.
    '''
    args = build_parser().parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        print(
            f"Refusing to bind {args.host}: this app has no authentication. "
            "Pass --allow-remote to override.",
            file=sys.stderr
        )
        return 2

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
