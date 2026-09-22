'''Versioned frontend asset serving for the web application.'''

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from .config import STATIC_DIR

ASSET_ALIASES = {
    "browser-api.js": "api.js",
    "live-updates.js": "sse.js",
}


@lru_cache(maxsize=None)
def read_asset(filename: str) -> tuple[str, str]:
    '''Return cached asset text and its short content hash.'''
    canonical_name = ASSET_ALIASES.get(filename, filename)
    path = STATIC_DIR / canonical_name
    if not path.is_file():
        raise HTTPException(status_code=500, detail=f"Frontend asset is missing: {filename}")
    raw = path.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()[:12]


def asset_url(filename: str) -> str:
    '''Return the immutable URL for a content-versioned asset.'''
    _content, version = read_asset(filename)
    path = Path(filename)
    return f"/static/{path.stem}.{version}{path.suffix}"


def serve_asset(
        filename: str,
        media_type: str,
        cache_control: str = "no-cache") -> Response:
    '''Serve an unversioned compatibility URL with a revalidation header.'''
    content, version = read_asset(filename)
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": cache_control, "ETag": f'"{version}"'},
    )


def serve_index() -> Response:
    '''Render the shell with the current immutable asset URLs.'''
    content, version = read_asset("index.html")
    content = content.replace(
        "/static/style.css", asset_url("style.css")
    ).replace(
        "/static/api.js", asset_url("api.js")
    ).replace(
        "/static/state.js", asset_url("state.js")
    ).replace(
        "/static/dom.js", asset_url("dom.js")
    ).replace(
        "/static/upload-staging.js", asset_url("upload-staging.js")
    ).replace(
        "/static/queue-view.js", asset_url("queue-view.js")
    ).replace(
        "/static/dialogs.js", asset_url("dialogs.js")
    ).replace(
        "/static/job-detail.js", asset_url("job-detail.js")
    ).replace(
        "/static/actions.js", asset_url("actions.js")
    ).replace(
        "/static/sse.js", asset_url("sse.js")
    ).replace(
        "/static/app.js", asset_url("app.js")
    )
    return Response(
        content,
        media_type="text/html",
        headers={"Cache-Control": "no-cache", "ETag": f'"{version}"'},
    )


def serve_versioned_asset(
        filename: str,
        version: str,
        media_type: str) -> Response:
    '''Serve a matching asset hash with an immutable cache policy.'''
    content, actual_version = read_asset(filename)
    if version != actual_version:
        raise HTTPException(status_code=404, detail="Asset version not found.")
    return Response(
        content,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{actual_version}"',
        },
    )
