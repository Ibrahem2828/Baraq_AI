"""Regression tests for the result cache (spec section 4 / Phase B3): the
same effective request (tenant + task + validated input/model policy +
source content versions + prompt/pipeline version) should reuse a
previously generated result instead of paying for another provider call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.services.result_cache as result_cache_module
from app.db.base import Base
from app.models.enums import ProviderAccount, TaskType
from app.models.result_cache import CachedAIResult
from app.pipelines.base import PipelineResult
from app.providers.base import ProviderResult, ProviderUsage
from app.schemas.common import Citation
from app.services.result_cache import ResultCacheService, compute_fingerprint


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element: object, compiler: object, **kw: object) -> str:
    # CachedAIResult uses Postgres's JSONB (production always runs on
    # Postgres); SQLite has no JSONB type at all, so this test-only compiler
    # override renders it as JSON here. Never touches the Postgres path.
    return "JSON"


def _fingerprint(**overrides: object) -> str:
    base: dict[str, object] = {
        "user_id": "user-1",
        "project_id": "project-1",
        "task_type": "fahes_generate_quiz",
        "input_hash": "a" * 64,
        "source_versions": {"source-1": "b" * 64},
        "prompt_checksum": "c" * 64,
        "pipeline_version": "2",
    }
    base.update(overrides)
    return compute_fingerprint(**base)  # type: ignore[arg-type]


def _result(*, cost: float = 0.05) -> PipelineResult:
    return PipelineResult(
        result_json={"quiz": "content"},
        citations=[Citation(source_id="source-1", page_number=1, excerpt="نص شاهد تجريبي")],
        provider_result=ProviderResult(
            data={"quiz": "content"},
            account=ProviderAccount.PRIMARY,
            model="gpt-5-mini",
            response_id="resp-1",
            usage=ProviderUsage(input_tokens=100, output_tokens=50, total_tokens=150),
            estimated_cost_usd=cost,
        ),
        quality_score=0.9,
        groundedness_score=0.95,
        warnings=[],
        security_flags=[],
    )


def test_fingerprint_is_deterministic() -> None:
    assert _fingerprint() == _fingerprint()


@pytest.mark.parametrize(
    "overrides",
    [
        {"user_id": "user-2"},
        {"project_id": "project-2"},
        {"task_type": "kholasa_generate_summary"},
        {"input_hash": "z" * 64},
        {"source_versions": {"source-1": "different-content-hash"}},
        {"prompt_checksum": "different-prompt-version"},
        {"pipeline_version": "3"},
    ],
)
def test_fingerprint_changes_when_any_component_changes(overrides: dict[str, object]) -> None:
    # Each of these must invalidate a cache hit on its own: a different user
    # (tenant isolation), a source's content changing (even with the same
    # source_id), or a prompt/pipeline version bump (a behavior change must
    # not keep serving results generated under the old behavior).
    assert _fingerprint(**overrides) != _fingerprint()


def test_khota_is_not_in_the_cacheable_task_types() -> None:
    # Its output is a day/task schedule anchored to the current date -- see
    # ResultCacheService's module docstring for why this must never be
    # served from a cache keyed only on static input.
    assert TaskType.KHOTA_GENERATE_PLAN not in ResultCacheService.CACHEABLE_TASK_TYPES
    assert TaskType.FAHES_GENERATE_QUIZ in ResultCacheService.CACHEABLE_TASK_TYPES


@pytest.fixture
async def cache_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ResultCacheService]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'result_cache.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[CachedAIResult.__table__])  # type: ignore[list-item]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # ResultCacheService.store() does `from app.db.session import
    # AsyncSessionLocal`, binding the name into its own module -- patch it
    # there (same pattern as provider_budget.py's tests).
    monkeypatch.setattr(result_cache_module, "AsyncSessionLocal", factory)

    async with factory() as session:
        yield ResultCacheService(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_lookup_returns_none_for_an_unknown_fingerprint(
    cache_service: ResultCacheService,
) -> None:
    assert (
        await cache_service.lookup(
            "no-such-fingerprint", user_id="user-1", project_id="project-1"
        )
        is None
    )


@pytest.mark.asyncio
async def test_store_then_lookup_round_trips_the_result(
    cache_service: ResultCacheService,
) -> None:
    fingerprint = _fingerprint()
    result = _result(cost=0.07)
    await cache_service.store(
        fingerprint=fingerprint,
        user_id="user-1",
        project_id="project-1",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        result=result,
    )
    cached = await cache_service.lookup(
        fingerprint, user_id="user-1", project_id="project-1"
    )
    assert cached is not None
    assert cached.result_json == {"quiz": "content"}
    assert cached.source_model_name == "gpt-5-mini"
    assert cached.source_provider_account == ProviderAccount.PRIMARY
    # The cached row keeps a record of what the *original* call cost, for
    # auditing -- but a cache HIT itself must never re-charge that amount
    # (job_processor.py builds a $0-cost ProviderResult for a hit).
    assert cached.source_estimated_cost_usd == 0.07


@pytest.mark.asyncio
async def test_lookup_rejects_same_fingerprint_from_another_project(
    cache_service: ResultCacheService,
) -> None:
    fingerprint = _fingerprint()
    await cache_service.store(
        fingerprint=fingerprint,
        user_id="user-1",
        project_id="project-1",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        result=_result(),
    )

    assert (
        await cache_service.lookup(
            fingerprint, user_id="user-1", project_id="project-2"
        )
        is None
    )


@pytest.mark.asyncio
async def test_lookup_bumps_hit_count_and_last_hit_at(
    cache_service: ResultCacheService,
) -> None:
    fingerprint = _fingerprint()
    await cache_service.store(
        fingerprint=fingerprint,
        user_id="user-1",
        project_id="project-1",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        result=_result(),
    )
    first_hit = await cache_service.lookup(
        fingerprint, user_id="user-1", project_id="project-1"
    )
    assert first_hit is not None
    # Same session identity-maps the row to the same Python object, so
    # capture hit_count immediately -- it's mutated in place by the next
    # lookup() call, not a fresh snapshot.
    first_hit_count = first_hit.hit_count
    second_hit = await cache_service.lookup(
        fingerprint, user_id="user-1", project_id="project-1"
    )
    assert second_hit is not None
    assert first_hit_count == 1
    assert second_hit.hit_count == 2
    assert second_hit.last_hit_at is not None


@pytest.mark.asyncio
async def test_store_does_not_raise_on_a_duplicate_fingerprint(
    cache_service: ResultCacheService,
) -> None:
    # Two concurrent jobs computing the exact same fingerprint (the same
    # effective request) both finish and try to cache -- the second write
    # must be a quiet no-op, not an unhandled IntegrityError that would
    # otherwise poison the caller's much larger job-completion transaction.
    fingerprint = _fingerprint()
    await cache_service.store(
        fingerprint=fingerprint,
        user_id="user-1",
        project_id="project-1",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        result=_result(cost=0.05),
    )
    await cache_service.store(
        fingerprint=fingerprint,
        user_id="user-1",
        project_id="project-1",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        result=_result(cost=0.99),  # a different result -- must be ignored
    )
    cached = await cache_service.lookup(
        fingerprint, user_id="user-1", project_id="project-1"
    )
    assert cached is not None
    assert cached.source_estimated_cost_usd == 0.05  # the first write wins
