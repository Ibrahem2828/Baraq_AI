from __future__ import annotations

from typing import Any, TypeVar, cast
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.endpoints import BACKEND_ENDPOINTS
from app.core.errors import AppError, AuthorizationError, ProviderError, ValidationFailure
from app.core.security import canonical_json_bytes, make_service_signature
from app.schemas.backend import CollectionManifest, LearnerContext, SourceManifest

T = TypeVar("T", bound=BaseModel)


class BackendClient:
    """Signed AI -> Django client; Django remains owner of all business state."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = httpx.AsyncClient(
            base_url=self.settings.baraq_backend_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.baraq_http_timeout_seconds),
            # A redirect could forward signed service headers to a different
            # origin. The configured Django base URL is the trust boundary.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        response_model: type[T] | None = None,
    ) -> T | dict[str, Any]:
        body = canonical_json_bytes(payload or {}) if payload is not None else b""
        headers = make_service_signature(method=method, target=path, body=body)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = await self.client.request(
                method,
                path,
                content=body if payload is not None else None,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Baraq backend is unreachable",
                code="backend_unreachable",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            try:
                details = response.json()
            except ValueError:
                details = {"body": response.text[:1000]}
            raise AppError(
                "Baraq backend rejected an internal AI request",
                code=str(details.get("code") or "backend_integration_error"),
                status_code=response.status_code,
                details=details,
                retryable=response.status_code >= 500,
            )
        data = response.json()
        if isinstance(data, dict) and "data" in data and data.get("success") is not False:
            data = data["data"]
        if response_model is None:
            return cast(dict[str, Any], data)
        return response_model.model_validate(data)

    @staticmethod
    def _with_user_id(path: str, user_id: str) -> str:
        return f"{path}?{urlencode({'user_id': user_id})}"

    async def get_source_manifest(self, *, source_id: str, user_id: str) -> SourceManifest:
        manifest = cast(
            SourceManifest,
            await self._request(
                "GET",
                self._with_user_id(BACKEND_ENDPOINTS.source_manifest(source_id), user_id),
                response_model=SourceManifest,
            ),
        )
        if manifest.owner_user_id != user_id:
            raise AuthorizationError(
                "The requested source does not belong to the job user", code="source_forbidden"
            )
        return manifest

    async def download_source(self, *, manifest: SourceManifest, user_id: str) -> bytes:
        """Download exactly the manifest's verified byte length through Django."""
        if manifest.owner_user_id != user_id:
            raise AuthorizationError(
                "The requested source does not belong to the job user", code="source_forbidden"
            )
        path = self._with_user_id(BACKEND_ENDPOINTS.source_download(manifest.source_id), user_id)
        headers = make_service_signature(method="GET", target=path, body=b"")
        try:
            content = bytearray()
            async with self.client.stream("GET", path, headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > manifest.size_bytes:
                        raise ValidationFailure(
                            "Downloaded source exceeds the manifest size",
                            code="source_size_mismatch",
                        )
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Unable to download the source file",
                code="source_download_failed",
                retryable=True,
            ) from exc
        if len(content) != manifest.size_bytes:
            raise ValidationFailure(
                "Downloaded source size does not match the backend manifest",
                code="source_size_mismatch",
            )
        return bytes(content)

    async def get_collection_manifest(self, *, collection_id: str) -> CollectionManifest:
        return cast(
            CollectionManifest,
            await self._request(
                "GET",
                BACKEND_ENDPOINTS.collection_manifest(collection_id),
                response_model=CollectionManifest,
            ),
        )

    async def get_learner_context(self, *, user_id: str) -> LearnerContext:
        return cast(
            LearnerContext,
            await self._request(
                "GET",
                BACKEND_ENDPOINTS.learner_context(user_id),
                response_model=LearnerContext,
            ),
        )

    async def deliver_job_webhook(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        """Deliver a signed, idempotent AI result event to the Django gateway."""
        return cast(
            dict[str, Any],
            await self._request("POST", BACKEND_ENDPOINTS.job_webhook, payload=payload),
        )
