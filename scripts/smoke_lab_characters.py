"""Provider-neutral standalone smoke run for the five Baraq Lab characters.

Runs under whatever ``PROVIDER_MODE`` is set in the environment (mock/replay
by default, live only if real Gemini/OpenAI credentials are configured). The
audio argument is required so Sada is tested with actual Local Whisper; the
script intentionally reports FAIL instead of inventing a transcript.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.application.standalone import BaraqAIApplication
from app.core.config import Settings
from app.core.errors import AppError
from app.lab.providers import build_lab_provider
from app.lab.storage import LabStorage
from app.models.enums import TaskType


async def run(*, audio_filename: str, audio_suffix: str, audio_content: bytes) -> int:
    settings = Settings(lab_storage_dir=Path(tempfile.mkdtemp(prefix="baraq-lab-smoke-")))
    provider = build_lab_provider(settings)
    print(
        f"LAB CHARACTER SMOKE: PROVIDER_MODE={settings.provider_mode} "
        f"provider={provider.provider_family.value}"
    )
    storage = LabStorage(settings)
    workspace = storage.ensure_workspace()
    text = storage.store_upload(
        workspace_id=workspace.workspace_id,
        filename="study.txt",
        mime_type="text/plain",
        content=(
            b"Newton's second law states that force equals mass multiplied by acceleration. "
            b"A net force changes motion."
        ),
    )
    audio = storage.store_upload(
        workspace_id=workspace.workspace_id,
        filename=audio_filename,
        mime_type={".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}[audio_suffix],
        content=audio_content,
    )
    application = BaraqAIApplication(settings=settings, storage=storage, provider=provider)
    today = date.today()
    requests: list[tuple[TaskType, dict[str, Any]]] = [
        (TaskType.KHOLASA_GENERATE_SUMMARY, {"source_ids": [text.source_id], "language": "en"}),
        (
            TaskType.FAHES_GENERATE_QUIZ,
            {
                "source_ids": [text.source_id],
                "question_count": 3,
                "question_types": ["mcq"],
                "language": "en",
            },
        ),
        (
            TaskType.KHOTA_GENERATE_PLAN,
            {
                "source_ids": [text.source_id],
                "subject_ids": ["physics"],
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=1)).isoformat(),
                "daily_available_minutes": 30,
                "preferred_session_minutes": 30,
                "language": "en",
            },
        ),
        (
            TaskType.RASHEED_RECOMMENDATIONS,
            {
                "metrics": [
                    {
                        "name": "quiz_accuracy",
                        "value": 60,
                        "unit": "percent",
                        "period": "Lab smoke run",
                        "authoritative": True,
                    }
                ],
                "language": "en",
            },
        ),
        (TaskType.SADA_TRANSCRIBE_AUDIO, {"source_id": audio.source_id, "language": "ar"}),
    ]
    for task_type, input_data in requests:
        try:
            output = await application.run_task(
                workspace_id=workspace.workspace_id,
                task_type=task_type,
                input=input_data,
            )
        except AppError as exc:
            print(f"LAB CHARACTER SMOKE: FAIL ({task_type.value}: {exc.code})")
            return 1
        if not output.result or output.validation.get("status") != "valid":
            print(f"LAB CHARACTER SMOKE: FAIL ({task_type.value}: invalid output)")
            return 1
        print(f"LAB CHARACTER: PASS ({task_type.value})")
    print("LAB CHARACTER SMOKE: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    args = parser.parse_args()
    if not args.audio.is_file() or args.audio.suffix.lower() not in {".mp3", ".wav", ".m4a"}:
        print("LAB CHARACTER SMOKE: FAIL (supply a readable .mp3, .wav, or .m4a with --audio)")
        return 2
    return asyncio.run(
        run(
            audio_filename=args.audio.name,
            audio_suffix=args.audio.suffix.lower(),
            audio_content=args.audio.read_bytes(),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
