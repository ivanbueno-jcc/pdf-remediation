'''
Stand in for an authenticating proxy so multi-user behaviour can be tried locally.

The web application reads the signed-in user from a header set by a proxy. A
browser cannot set that header on a normal navigation, so exercising multi-user
mode by hand needs something in front that injects it. Run one of these per
person on its own port, then open each port in a separate browser window to be
several people at once.

This is a development aid. It performs no authentication whatever: it asserts
whichever identity you name on the command line, which is the entire point.
Never run it in front of a deployment that anyone else can reach.
'''

from __future__ import annotations

import argparse
import sys

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover - developer environment only
    raise SystemExit(
        "This development tool needs the dev dependencies. Run: uv sync --all-groups"
    ) from None

import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

# Hop-by-hop headers must not be forwarded; Content-Length is recomputed by the
# client, and forwarding a stale one corrupts uploads.
DROPPED_REQUEST_HEADERS = frozenset({
    "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authorization", "proxy-authenticate", "te", "trailer",
    "content-length",
})
DROPPED_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "content-length", "content-encoding",
})


def build_app(target: str, user: str, identity_header: str,
              secret_header: str, secret: str) -> Starlette:
    '''
    Build an ASGI app that forwards every request with an injected identity.
    '''
    client = httpx.AsyncClient(base_url=target, timeout=None)

    async def forward(request: Request) -> StreamingResponse:
        '''
        Stream one request to the application and stream the response back.
        '''
        headers = {
            name: value for name, value in request.headers.items()
            if name.lower() not in DROPPED_REQUEST_HEADERS
        }
        # Anything the caller sent under these names is replaced, never merged:
        # a real proxy strips client-supplied identity headers too.
        headers[identity_header] = user
        if secret:
            headers[secret_header] = secret

        upstream = client.build_request(
            request.method,
            request.url.path,
            params=request.url.query,
            headers=headers,
            content=request.stream(),
        )
        response = await client.send(upstream, stream=True)

        return StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            headers={
                name: value for name, value in response.headers.items()
                if name.lower() not in DROPPED_RESPONSE_HEADERS
            },
            background=BackgroundTask(response.aclose),
        )

    return Starlette(routes=[
        Route("/{path:path}", forward,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    ])


def build_parser() -> argparse.ArgumentParser:
    '''
    Build the CLI parser.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Development-only proxy that asserts a fixed identity. "
            "Run one per simulated user, each on its own port."
        )
    )
    parser.add_argument("user", help="Identity to assert, e.g. alice@example.com.")
    parser.add_argument(
        "--port", type=int, required=True,
        help="Port for this simulated user to browse."
    )
    parser.add_argument(
        "--target", default="http://127.0.0.1:8000",
        help="The running web application (default: %(default)s)."
    )
    parser.add_argument(
        "--identity-header", default="x-forwarded-email",
        help="Header to inject, matching PDF_WEB_IDENTITY_HEADER (default: %(default)s)."
    )
    parser.add_argument(
        "--secret", default="",
        help="Value for PDF_WEB_PROXY_SECRET, if the app requires one."
    )
    parser.add_argument(
        "--secret-header", default="x-pdf-web-proxy-secret",
        help="Header carrying the secret (default: %(default)s)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    '''
    Run one simulated user's proxy.
    '''
    args = build_parser().parse_args(argv)
    print(
        f"Simulating {args.user} on http://127.0.0.1:{args.port} "
        f"-> {args.target}\n"
        "Development only: this proxy authenticates nobody."
    )
    uvicorn.run(
        build_app(
            args.target,
            args.user,
            args.identity_header.lower(),
            args.secret_header.lower(),
            args.secret,
        ),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
