from __future__ import annotations

from app.models.enums import TaskType
from app.pipelines.base import AIPipeline
from app.pipelines.fahes import FahesPipeline
from app.pipelines.kholasa import KholasaPipeline
from app.pipelines.khota import KhotaPipeline
from app.pipelines.rasheed import RasheedPipeline
from app.pipelines.sada import SadaPipeline

_PIPELINES: dict[TaskType, type[AIPipeline]] = {
    TaskType.FAHES_GENERATE_QUIZ: FahesPipeline,
    TaskType.KHOTA_GENERATE_PLAN: KhotaPipeline,
    TaskType.RASHEED_RECOMMENDATIONS: RasheedPipeline,
    TaskType.KHOLASA_GENERATE_SUMMARY: KholasaPipeline,
    TaskType.SADA_TRANSCRIBE_AUDIO: SadaPipeline,
}


def get_pipeline(task_type: TaskType) -> AIPipeline:
    try:
        return _PIPELINES[task_type]()
    except KeyError as exc:
        raise ValueError(f"No pipeline registered for {task_type}") from exc
