from __future__ import annotations

import asyncio
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from backend.services.langgraph_checkpoints import (
    close_langgraph_checkpoints,
    get_langgraph_checkpointer,
    start_langgraph_checkpoints,
)


class CheckState(TypedDict):
    value: int


async def check_checkpoint_resume() -> None:
    await start_langgraph_checkpoints()
    saver = get_langgraph_checkpointer()
    run_id = uuid4()
    config = agent_checkpoint_config(run_id)
    fail_once = True

    async def work(state: CheckState) -> CheckState:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("simulated failure")
        return {"value": state["value"] + 1}

    builder = StateGraph(CheckState)
    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile(checkpointer=saver)
    try:
        try:
            await graph.ainvoke({"value": 41}, config=config)
        except RuntimeError as exc:
            if str(exc) != "simulated failure":
                raise
        else:
            raise AssertionError("Graph thử nghiệm phải lỗi ở lần chạy đầu")

        checkpoint = await saver.aget_tuple(config)
        if checkpoint is None:
            raise AssertionError("Không tìm thấy checkpoint PostgreSQL")

        result = await graph.ainvoke(None, config=config)
        if result["value"] != 42:
            raise AssertionError("Graph không resume đúng state đã lưu")
        print(f"thread_id={agent_thread_id(run_id)}")
        print("POSTGRES_CHECKPOINT_RESUME_PASSED")
    finally:
        await saver.adelete_thread(agent_thread_id(run_id))
        await close_langgraph_checkpoints()


def main() -> None:
    asyncio.run(check_checkpoint_resume())


if __name__ == "__main__":
    main()
