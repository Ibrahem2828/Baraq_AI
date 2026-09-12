from __future__ import annotations

import orjson
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MaxRequestBodyMiddleware:
    """Reject an oversized request body before any route or auth code runs.

    Blueprint 02_AI_PLATFORM.md §3.1 (Layer 0 Trust Gate) requires the
    payload size be checked before any AI processing -- this cannot be left
    to an external reverse proxy the service isn't guaranteed to sit behind
    (its own nginx config is not part of this codebase, and the service is
    directly reachable on the internal network either way).

    Implemented as a plain ASGI callable, not starlette.middleware.base's
    BaseHTTPMiddleware, which buffers the entire body into memory itself
    before a handler ever sees it -- exactly the thing this must avoid.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self.max_bytes
        ):
            await self._reject(send)
            return

        total = 0
        rejected = False

        async def guarded_receive() -> Message:
            nonlocal total, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body") or b"")
                if total > self.max_bytes:
                    rejected = True
                    # Send the real response ourselves, then tell the inner
                    # app the client disconnected so it stops reading and
                    # never itself calls send() -- avoids a double response.
                    await self._reject(send)
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, guarded_receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = orjson.dumps(
            {
                "success": False,
                "error": {
                    "code": "payload_too_large",
                    "message": "Request body exceeds the configured size limit",
                    "details": None,
                    "request_id": None,
                },
            }
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
