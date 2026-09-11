"""Browser-facing standalone Lab API and Jinja2 UI (spec section 30).

Nine independently addressable pages: overview, sources-rag, prompts, and one
console per character (fahes/kholasa/khota/rasheed/sada), plus
evals-providers. Every run is provider-neutral (PROVIDER_MODE=mock|replay|
live) and never calls Django, real Postgres, Redis or Celery -- this stays a
local engineering console, not a production path. It is only ever mounted
when ``ENABLE_AI_LAB=true`` and ``APP_ENV != "production"`` (see
``app/main.py``), or in the legacy standalone ``BARAQ_RUNTIME_MODE=lab``
process.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.application.standalone import BaraqAIApplication
from app.core.errors import ValidationFailure
from app.lab.jobs import LocalJobManager
from app.lab.storage import LabStorage
from app.models.enums import TaskType
from app.prompts.registry import get_prompt_registry
from app.providers.base import LLMProvider
from app.providers.mock_provider import MockProvider
from app.providers.model_aliases import model_for_tier

router = APIRouter(prefix="/lab", tags=["Baraq AI Lab"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[2] / "lab" / "templates")
)

# The nine mandatory pages (spec section 30), in nav order.
LAB_PAGES: dict[str, dict[str, str]] = {
    "overview": {"title": "نظرة عامة", "path": "/lab/overview"},
    "sources-rag": {"title": "المصادر و RAG", "path": "/lab/sources-rag"},
    "prompts": {"title": "سجل الأوامر (Prompts)", "path": "/lab/prompts"},
    "fahes": {"title": "فاحص", "path": "/lab/fahes"},
    "kholasa": {"title": "خلاصة", "path": "/lab/kholasa"},
    "khota": {"title": "خُطى", "path": "/lab/khota"},
    "rasheed": {"title": "رشيد", "path": "/lab/rasheed"},
    "sada": {"title": "صدى", "path": "/lab/sada"},
    "evals-providers": {"title": "تقييم المزودات", "path": "/lab/evals-providers"},
}

TASK_BY_TAB: dict[str, str] = {
    "fahes": TaskType.FAHES_GENERATE_QUIZ.value,
    "kholasa": TaskType.KHOLASA_GENERATE_SUMMARY.value,
    "khota": TaskType.KHOTA_GENERATE_PLAN.value,
    "rasheed": TaskType.RASHEED_RECOMMENDATIONS.value,
    "sada": TaskType.SADA_TRANSCRIBE_AUDIO.value,
}


class LabRunRequest(BaseModel):
    task_type: TaskType
    input: dict[str, Any] = Field(default_factory=dict)
    thinking: bool | None = None


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    flags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class QuizAttemptRequest(BaseModel):
    answers: dict[str, int] = Field(default_factory=dict)
    correct_answers: dict[str, int] = Field(default_factory=dict)


def _storage(request: Request) -> LabStorage:
    return cast(LabStorage, request.app.state.lab_storage)


def _application(request: Request) -> BaraqAIApplication:
    return cast(BaraqAIApplication, request.app.state.lab_application)


def _jobs(request: Request) -> LocalJobManager:
    return cast(LocalJobManager, request.app.state.lab_jobs)


def _provider(request: Request) -> LLMProvider:
    return _application(request).provider


async def _render_page(request: Request, workspace_id: str | None, active_tab: str) -> Response:
    storage = _storage(request)
    if workspace_id is None:
        workspace = storage.ensure_workspace()
        return RedirectResponse(
            url=f"/lab/{active_tab}?workspace_id={workspace.workspace_id}", status_code=303
        )
    workspace = storage.ensure_workspace(workspace_id)
    settings = _application(request).settings
    provider = _provider(request)
    configured_models = sorted(
        {
            model_for_tier(settings, provider.provider_family, tier)
            for tier in ("fast", "balanced", "high_quality")
        }
    )
    context: dict[str, Any] = {
        "workspace": workspace,
        "sources": storage.list_sources(workspace_id=workspace.workspace_id),
        "provider_mode": settings.provider_mode,
        "provider_family": provider.provider_family.value,
        "configured_models": configured_models,
        "runtime_mode": settings.baraq_runtime_mode,
        "active_tab": active_tab,
        "pages": LAB_PAGES,
        "TASK_BY_TAB": TASK_BY_TAB,
        "prompts": [],
        "recent_jobs": [],
    }
    if active_tab == "prompts":
        context["prompts"] = get_prompt_registry().list()
    if active_tab == "evals-providers":
        context["recent_jobs"] = storage.list_jobs(workspace_id=workspace.workspace_id, limit=20)
    return templates.TemplateResponse(request=request, name="lab.html", context=context)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def lab_home(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "overview")


@router.get("/overview", response_class=HTMLResponse, include_in_schema=False)
async def lab_overview(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "overview")


@router.get("/sources-rag", response_class=HTMLResponse, include_in_schema=False)
async def lab_sources_rag(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "sources-rag")


@router.get("/prompts", response_class=HTMLResponse, include_in_schema=False)
async def lab_prompts(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "prompts")


@router.get("/fahes", response_class=HTMLResponse, include_in_schema=False)
async def lab_fahes(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "fahes")


@router.get("/kholasa", response_class=HTMLResponse, include_in_schema=False)
async def lab_kholasa(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "kholasa")


@router.get("/khota", response_class=HTMLResponse, include_in_schema=False)
async def lab_khota(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "khota")


@router.get("/rasheed", response_class=HTMLResponse, include_in_schema=False)
async def lab_rasheed(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "rasheed")


@router.get("/sada", response_class=HTMLResponse, include_in_schema=False)
async def lab_sada(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "sada")


@router.get("/evals-providers", response_class=HTMLResponse, include_in_schema=False)
async def lab_evals_providers(request: Request, workspace_id: str | None = None) -> Response:
    return await _render_page(request, workspace_id, "evals-providers")


@router.get("/health")
async def lab_health(request: Request) -> JSONResponse:
    settings = _application(request).settings
    provider = _provider(request)
    configured_models = sorted(
        {
            model_for_tier(settings, provider.provider_family, tier)
            for tier in ("fast", "balanced", "high_quality")
        }
    )
    fell_back_to_mock = settings.provider_mode == "live" and isinstance(provider, MockProvider)
    return JSONResponse(
        {
            "runtime_mode": settings.baraq_runtime_mode,
            "provider_mode": settings.provider_mode,
            "provider": provider.provider_family.value,
            "stt_provider": "local_whisper",
            "configured": not fell_back_to_mock,
            "configured_models": configured_models,
            "message": (
                "PROVIDER_MODE=live لكن لا يوجد مفتاح Gemini أو OpenAI مضبوط؛ "
                "تم الرجوع تلقائياً إلى Mock ولا يوجد اتصال حي."
                if fell_back_to_mock
                else None
            ),
        }
    )


@router.post("/workspaces/{workspace_id}/sources", status_code=201)
async def upload_sources(
    request: Request,
    workspace_id: str,
    files: Annotated[list[UploadFile], File(...)],
) -> JSONResponse:
    storage = _storage(request)
    stored = []
    for uploaded in files:
        stored.append(
            storage.store_upload(
                workspace_id=workspace_id,
                filename=uploaded.filename or "upload.bin",
                mime_type=uploaded.content_type,
                content=await uploaded.read(),
            )
        )
    return JSONResponse(
        status_code=201,
        content={
            "sources": [
                {
                    "source_id": source.source_id,
                    "filename": source.filename,
                    "status": source.status,
                    "size_bytes": source.size_bytes,
                    "metadata": source.metadata,
                }
                for source in stored
            ]
        },
    )


@router.post("/upload", response_class=HTMLResponse, include_in_schema=False)
async def upload_sources_form(
    request: Request,
    workspace_id: Annotated[str, Form(...)],
    files: Annotated[list[UploadFile], File(...)],
) -> RedirectResponse:
    storage = _storage(request)
    for uploaded in files:
        storage.store_upload(
            workspace_id=workspace_id,
            filename=uploaded.filename or "upload.bin",
            mime_type=uploaded.content_type,
            content=await uploaded.read(),
        )
    return RedirectResponse(url=f"/lab/sources-rag?workspace_id={workspace_id}", status_code=303)


@router.get("/workspaces/{workspace_id}/sources/{source_id}/chunks")
async def source_chunks(request: Request, workspace_id: str, source_id: str) -> JSONResponse:
    storage = _storage(request)
    source = storage.get_source(source_id=source_id, workspace_id=workspace_id)
    chunks = storage.list_chunks(workspace_id=workspace_id, source_ids=[source_id])
    return JSONResponse(
        {
            "source": {
                "source_id": source.source_id,
                "filename": source.filename,
                "metadata": source.metadata,
            },
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "index": chunk.chunk_index,
                    "page_number": chunk.page_number,
                    "section_title": chunk.section_title,
                    "text": chunk.text,
                }
                for chunk in chunks
            ],
        }
    )


@router.post("/workspaces/{workspace_id}/runs", status_code=202)
async def create_run(request: Request, workspace_id: str, body: LabRunRequest) -> JSONResponse:
    storage = _storage(request)
    job = storage.create_job(
        workspace_id=workspace_id,
        task_type=body.task_type.value,
        request={"input": body.input, "thinking": body.thinking},
    )
    _jobs(request).submit(
        job_id=job.job_id,
        workspace_id=workspace_id,
        task_type=body.task_type,
        input=body.input,
        thinking=body.thinking,
    )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.job_id,
            "status": job.status,
            "status_url": f"/lab/workspaces/{workspace_id}/jobs/{job.job_id}",
        },
    )


@router.get("/workspaces/{workspace_id}/jobs/{job_id}")
async def get_run(request: Request, workspace_id: str, job_id: str) -> JSONResponse:
    return JSONResponse(
        _job_payload(_storage(request).get_job(job_id=job_id, workspace_id=workspace_id))
    )


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}/card",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def get_run_card(request: Request, workspace_id: str, job_id: str) -> HTMLResponse:
    job = _storage(request).get_job(job_id=job_id, workspace_id=workspace_id)
    return templates.TemplateResponse(
        request=request,
        name="job_card.html",
        context={
            "job": job,
            "result_pretty": json.dumps(job.result_json, ensure_ascii=False, indent=2)
            if job.result_json
            else None,
        },
    )


@router.get("/workspaces/{workspace_id}/jobs/{job_id}/export")
async def export_run(request: Request, workspace_id: str, job_id: str) -> JSONResponse:
    job = _storage(request).get_job(job_id=job_id, workspace_id=workspace_id)
    if job.result_json is None:
        raise ValidationFailure(
            "The job has no completed result to export", code="lab_export_not_ready"
        )
    return JSONResponse({"job": _job_payload(job), "export": job.result_json})


@router.post("/workspaces/{workspace_id}/jobs/{job_id}/transcript-source", status_code=201)
async def save_transcript_source(
    request: Request, workspace_id: str, job_id: str, variant: str = "cleaned"
) -> JSONResponse:
    if variant not in {"raw", "cleaned"}:
        raise ValidationFailure(
            "Transcript variant must be raw or cleaned", code="lab_invalid_variant"
        )
    storage = _storage(request)
    job = storage.get_job(job_id=job_id, workspace_id=workspace_id)
    if job.task_type != TaskType.SADA_TRANSCRIBE_AUDIO.value or not job.result_json:
        raise ValidationFailure(
            "Only a completed Sada job has a transcript", code="lab_transcript_not_ready"
        )
    result = job.result_json.get("result") or {}
    field = "full_transcript" if variant == "raw" else "cleaned_transcript"
    transcript = result.get(field)
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValidationFailure(
            "The requested transcript is unavailable", code="lab_transcript_not_ready"
        )
    source = storage.store_upload(
        workspace_id=workspace_id,
        filename=f"sada-{job_id}-{variant}.txt",
        mime_type="text/plain",
        content=transcript.encode("utf-8"),
    )
    return JSONResponse(
        status_code=201,
        content={
            "source_id": source.source_id,
            "filename": source.filename,
            "status": source.status,
        },
    )


@router.post("/workspaces/{workspace_id}/jobs/{job_id}/feedback", status_code=204)
async def submit_feedback(
    request: Request, workspace_id: str, job_id: str, body: FeedbackRequest
) -> HTMLResponse:
    _storage(request).record_feedback(
        workspace_id=workspace_id,
        job_id=job_id,
        rating=body.rating,
        flags=body.flags,
        notes=body.notes,
    )
    return HTMLResponse(status_code=204)


@router.post("/workspaces/{workspace_id}/jobs/{job_id}/quiz-attempt")
async def submit_quiz_attempt(
    request: Request, workspace_id: str, job_id: str, body: QuizAttemptRequest
) -> JSONResponse:
    matched = sum(
        1
        for question_id, answer in body.answers.items()
        if body.correct_answers.get(question_id) == answer
    )
    total = len(body.correct_answers)
    metrics = {
        "correct_answers": matched,
        "question_count": total,
        "accuracy_percent": round((matched / total) * 100, 2) if total else 0.0,
        "source": "lab_quiz_session",
    }
    _storage(request).record_quiz_attempt(
        workspace_id=workspace_id,
        job_id=job_id,
        answers=body.answers,
        metrics=metrics,
    )
    return JSONResponse(metrics)


def _job_payload(job: Any) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "task_type": job.task_type,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "progress_message": job.progress_message,
        "result": job.result_json,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
