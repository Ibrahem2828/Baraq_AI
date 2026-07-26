from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import AppError, ProviderError
from app.core.endpoints import BACKEND_ENDPOINTS
from app.core.security import canonical_json_bytes, make_service_signature
from app.schemas.backend import (
    CreditCommitRequest,
    CreditReservationRequest,
    CreditReservationResponse,
    LearnerContext,
    MaterializeRequest,
    MaterializeResponse,
    SourceManifest,
)

T = TypeVar("T", bound=BaseModel)


class BackendClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = httpx.AsyncClient(
            base_url=self.settings.baraq_backend_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.baraq_http_timeout_seconds),
            follow_redirects=True,
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
        headers = make_service_signature(method=method, path=path, body=body)
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
            return data
        return response_model.model_validate(data)

    async def get_source_manifest(self, *, source_id: str, user_id: str) -> SourceManifest:
        return await self._request(
            "GET",
            BACKEND_ENDPOINTS.source_manifest(source_id, user_id),
            response_model=SourceManifest,
        )

    async def download_source(self, manifest: SourceManifest) -> bytes:
        try:
            response = await self.client.get(str(manifest.download_url))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Unable to download the source file",
                code="source_download_failed",
                retryable=True,
            ) from exc
        return response.content

    async def get_learner_context(self, *, user_id: str) -> LearnerContext:
        return await self._request(
            "GET",
            BACKEND_ENDPOINTS.learner_context(user_id),
            response_model=LearnerContext,
        )

    async def reserve_credits(
        self, request: CreditReservationRequest
    ) -> CreditReservationResponse:
        return await self._request(
            "POST",
            BACKEND_ENDPOINTS.credits_reserve,
            payload=request.model_dump(mode="json"),
            response_model=CreditReservationResponse,
        )

    async def commit_credits(self, request: CreditCommitRequest) -> dict[str, Any]:
        return await self._request(
            "POST",
            BACKEND_ENDPOINTS.credits_commit,
            payload=request.model_dump(mode="json"),
        )

    async def refund_credits(self, *, reservation_id: str, reason: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            BACKEND_ENDPOINTS.credits_refund,
            payload={"reservation_id": reservation_id, "reason": reason},
        )

    async def materialize(self, request: MaterializeRequest) -> MaterializeResponse:
        return await self._request(
            "POST",
            BACKEND_ENDPOINTS.materialize,
            payload=request.model_dump(mode="json"),
            response_model=MaterializeResponse,
        )
