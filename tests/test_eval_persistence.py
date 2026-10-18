from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from agent_core.evaluation import fingerprint_eval_dataset, load_eval_dataset
from agent_core.prompts import SYSTEM_PROMPT_FINGERPRINT, SYSTEM_PROMPT_VERSION
from backend.models import EvalRun
from backend.services.evaluation import (
    EVAL_JUDGE_PROMPT_FINGERPRINT,
    EVAL_JUDGE_PROMPT_VERSION,
    create_eval_run,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    provider = "fake-provider"
    model = "fake-model-v2"


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, value: object) -> None:
        if isinstance(value, EvalRun) and value.id is None:
            value.id = uuid4()


async def test_create_eval_run_snapshots_all_versions() -> None:
    dataset = load_eval_dataset()
    cases = dataset.cases[:2]
    session = FakeSession()

    run = await create_eval_run(  # type: ignore[arg-type]
        session,
        dataset=dataset,
        cases=cases,
        model=FakeModel(),  # type: ignore[arg-type]
    )

    assert run.dataset_id == dataset.dataset_id
    assert run.dataset_version == dataset.version
    assert run.dataset_fingerprint == fingerprint_eval_dataset(dataset)
    assert run.provider == "fake-provider"
    assert run.model_name == "fake-model-v2"
    assert run.prompt_version == SYSTEM_PROMPT_VERSION
    assert run.prompt_fingerprint == SYSTEM_PROMPT_FINGERPRINT
    assert run.judge_prompt_version == EVAL_JUDGE_PROMPT_VERSION
    assert run.judge_prompt_fingerprint == EVAL_JUDGE_PROMPT_FINGERPRINT
    assert run.selected_case_ids == [case.case_id for case in cases]
    assert run.total_cases == 2
    assert session.commits == 1


def test_eval_migration_follows_current_head_and_creates_both_tables() -> None:
    migration = (
        PROJECT_ROOT / "migrations/versions/20260929_0020_create_eval_tables.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "20260929_0020"' in migration
    assert 'down_revision: str | Sequence[str] | None = "20260928_0019"' in migration
    assert '"eval_runs"' in migration
    assert '"eval_case_results"' in migration
    assert '"ix_eval_runs_version_lookup"' in migration
