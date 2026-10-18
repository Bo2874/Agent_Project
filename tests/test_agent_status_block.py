from agent_core.status import AgentStatusBlock, AgentStatusBudget


def make_status() -> AgentStatusBlock:
    return AgentStatusBlock(
        goal="Tính 12 nhân 4",
        progress=("Đã tiếp nhận yêu cầu.",),
        remaining_work=("Gọi tool phù hợp.",),
        budget=AgentStatusBudget(
            current_step=2,
            max_steps=8,
            used_tokens=120,
            max_tokens=20_000,
            elapsed_seconds=1.24,
            max_seconds=120,
        ),
    )


def test_status_block_exposes_structured_progress_and_remaining_budget() -> None:
    payload = make_status().to_payload()

    assert payload["goal"] == "Tính 12 nhân 4"
    assert payload["progress"] == ["Đã tiếp nhận yêu cầu."]
    assert payload["remaining_work"] == ["Gọi tool phù hợp."]
    assert payload["budget"]["steps"] == {"current": 2, "limit": 8, "remaining": 7}
    assert payload["budget"]["tokens"]["remaining"] == 19_880
    assert payload["budget"]["time_seconds"]["elapsed"] == 1.2
    assert payload["budget"]["time_seconds"]["remaining"] == 118.8
