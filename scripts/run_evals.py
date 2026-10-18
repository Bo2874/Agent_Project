"""Run the versioned eval dataset through the real agent loop and persist results."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agent_core.context_budget import ContextTokenBudget
from agent_core.evaluation import (
    DEFAULT_EVAL_DATASET_PATH,
    EvalCase,
    EvalCategory,
    load_eval_dataset,
)
from agent_core.graph import AgentLimits
from agent_core.skills import SkillSelectionConfig
from agent_core.tools import ToolSelectionConfig
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.dependencies_llm import create_chat_model
from backend.dependencies_skills import get_skill_registry
from backend.dependencies_tools import (
    close_tool_connections,
    get_tool_registry,
    start_tool_connections,
)
from backend.models import EvalCaseResult
from backend.services.evaluation import (
    AgentEvalExecutor,
    ModelEvalJudge,
    run_eval_suite,
)
from backend.services.langsmith_evaluation import create_langsmith_evaluation_service
from backend.services.langsmith_tracing import (
    close_langsmith_tracing,
    start_langsmith_tracing,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chạy eval tự động và lưu kết quả versioned vào PostgreSQL."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_EVAL_DATASET_PATH,
        help="Đường dẫn dataset JSON.",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=[item.value for item in EvalCategory],
        help="Chỉ chạy category này; có thể truyền nhiều lần.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        help="Chỉ chạy case ID này; có thể truyền nhiều lần.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Giới hạn số case sau khi lọc, phù hợp để smoke test.",
    )
    return parser.parse_args()


def select_cases(
    cases: tuple[EvalCase, ...],
    *,
    categories: list[str] | None,
    case_ids: list[str] | None,
    limit: int | None,
) -> tuple[EvalCase, ...]:
    selected = cases
    if categories:
        allowed_categories = set(categories)
        selected = tuple(case for case in selected if case.category.value in allowed_categories)
    if case_ids:
        allowed_case_ids = set(case_ids)
        unknown = allowed_case_ids - {case.case_id for case in cases}
        if unknown:
            raise ValueError(f"Không tìm thấy case: {sorted(unknown)}")
        selected = tuple(case for case in selected if case.case_id in allowed_case_ids)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit phải lớn hơn hoặc bằng 1")
        selected = selected[:limit]
    if not selected:
        raise ValueError("Bộ lọc không chọn được eval case nào")
    return selected


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    dataset = load_eval_dataset(args.dataset)
    cases = select_cases(
        dataset.cases,
        categories=args.category,
        case_ids=args.case_id,
        limit=args.limit,
    )
    model = create_chat_model()
    start_langsmith_tracing()
    langsmith_evaluation = create_langsmith_evaluation_service()

    await start_tool_connections()
    executor = AgentEvalExecutor(
        model=model,
        registry=get_tool_registry(),
        skill_registry=get_skill_registry(),
        limits=AgentLimits(
            max_steps=settings.agent_max_steps,
            max_total_tokens=settings.agent_max_total_tokens,
            max_duration_seconds=settings.agent_max_duration_seconds,
            max_identical_tool_calls=settings.agent_max_identical_tool_calls,
            max_repeated_observations=settings.agent_max_repeated_observations,
        ),
        context_budget=ContextTokenBudget(
            max_input_tokens=settings.context_max_input_tokens,
            system_tokens=settings.context_system_token_budget,
            memory_tokens=settings.context_memory_token_budget,
            history_tokens=settings.context_history_token_budget,
            tool_tokens=settings.context_tool_token_budget,
            rag_tokens=settings.context_rag_token_budget,
        ),
        tool_selection_config=ToolSelectionConfig(
            lazy_load_threshold=settings.tool_lazy_load_threshold,
            max_loaded_tools=settings.tool_max_loaded,
        ),
        skill_selection_config=SkillSelectionConfig(
            minimum_score=settings.skill_selection_min_score,
            ambiguity_margin=settings.skill_selection_ambiguity_margin,
        ),
    )
    judge = ModelEvalJudge(model)

    def show_progress(result: EvalCaseResult) -> None:
        detail = f"{result.score}/100" if result.score is not None else result.error_message
        print(f"[{result.status.upper():7}] {result.case_id}: {detail}")

    try:
        run_id = await run_eval_suite(
            session_factory=SessionFactory,
            dataset=dataset,
            cases=cases,
            model=model,
            executor=executor,
            judge=judge,
            progress_handler=show_progress,
            experiment_publisher=langsmith_evaluation,
        )
        print(f"Eval run hoàn thành: {run_id}")
        print(
            f"Dataset {dataset.dataset_id}@{dataset.version}; model {model.model}; "
            f"prompt được lưu kèm fingerprint; số case: {len(cases)}."
        )
        if langsmith_evaluation.last_experiment_name is not None:
            print(f"LangSmith experiment: {langsmith_evaluation.last_experiment_name}")
        else:
            print("LangSmith: đang tắt; kết quả chỉ được lưu trong PostgreSQL.")
    finally:
        await model.aclose()
        await close_tool_connections()
        await close_langsmith_tracing()
        await engine.dispose()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Eval đã dừng.")


if __name__ == "__main__":
    main()
