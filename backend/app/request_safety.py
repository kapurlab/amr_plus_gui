"""Same-origin guard for browser-initiated state changes."""
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse


def install_request_safety(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.middleware("http")
    async def reject_cross_site_mutations(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("sec-fetch-site", "").lower() == "cross-site"
        ):
            return PlainTextResponse("forbidden (cross-site request)", status_code=403)
        return await call_next(request)
