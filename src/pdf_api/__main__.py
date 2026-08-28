'''
Command line entry point for the PDF remediation API.
'''

from __future__ import annotations

import argparse
import sys

import uvicorn

from . import APP_NAME, APP_VERSION

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def build_parser() -> argparse.ArgumentParser:
    '''
    Build the CLI parser.
    '''
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} {APP_VERSION}: remediate one PDF over HTTP."
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to bind (default: %(default)s).")
    parser.add_argument("--port", type=int, default=8100,
                        help="Port to listen on (default: %(default)s).")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Permit a non-loopback bind. This API has no authentication.")
    return parser


def main(argv: list[str] | None = None) -> int:
    '''
    Start the API with a single worker process.

    Job state lives in this process, so the service must never be spread across
    multiple uvicorn workers.
    '''
    args = build_parser().parse_args(argv)

    if args.host not in LOOPBACK_HOSTS and not args.allow_remote:
        print(
            f"Refusing to bind {args.host}: this API has no authentication. "
            "Pass --allow-remote to override, and put it behind something that does.",
            file=sys.stderr,
        )
        return 2

    uvicorn.run("pdf_api.app:app", host=args.host, port=args.port,
                workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
