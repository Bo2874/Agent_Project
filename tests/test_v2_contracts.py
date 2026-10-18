import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from jsonschema.validators import validator_for
from pydantic import ValidationError

from agent_core.contracts import (
    RUN_EVENT_ADAPTER,
    CapabilityDescriptor,
    PlanCreatedEvent,
    RetrievalCompletedEvent,
    RetrievalResult,
    RoutingDecidedEvent,
    RoutingDecision,
    RunStatusEvent,
    TaskSpec,
    TaskStatusEvent,
)


def capability_payload() -> dict:
    return {
        "capability_id": "demo__calculate",
        "kind": "tool",
        "name": "Calculate",
        "description": "Perform an exact calculation",
        "version": "1.0.0",
        "side_effect": "read_only",
        "requires_approval": False,
    }


def routing_payload() -> dict:
    return {
        "route": "tool_task",
        "candidate_tool_ids": ["demo__calculate", "demo__count_text"],
        "selected_tool_ids": ["demo__calculate"],
        "probabilities": {"tool_task": 0.9, "chat": 0.1},
        "confidence": 0.8,
        "provider": "jev",
        "model_version": "jev-1.13.0",
        "policy_version": "routing-v1",
        "catalog_version": "catalog-v1",
        "latency_ms": 100.0,
        "input_tokens": 120,
        "reason_code": "tool_match",
    }


def evidence_payload() -> dict:
    return {
        "citation_id": "source_1",
        "document_id": str(uuid4()),
        "version_id": str(uuid4()),
        "chunk_id": str(uuid4()),
        "title": "Agent handbook",
        "text": "  Original quote.\n",
        "score": 0.75,
        "score_kind": "similarity",
        "retrieval_config_version": "hybrid-v1",
        "page_start": 3,
        "page_end": 4,
    }


def task_payload() -> dict:
    return {
        "task_id": "read_a",
        "goal": "Read the relevant evidence from document A",
        "agent_type": "document_researcher",
        "allowed_capability_ids": ["documents.search"],
        "output_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        "acceptance_criteria": ["All factual claims reference source passages"],
        "timeout_seconds": 30.0,
        "budget": {"max_tokens": 2000, "max_tool_calls": 2, "max_steps": 4},
        "side_effect": "read_only",
    }


def event_envelope() -> dict:
    return {
        "event_id": str(uuid4()),
        "root_run_id": str(uuid4()),
        "sequence": 1,
        "occurred_at": "2026-10-18T16:00:00+07:00",
    }


@pytest.mark.parametrize(
    "model,payload",
    [
        (CapabilityDescriptor, capability_payload),
        (RoutingDecision, routing_payload),
        (RetrievalResult, evidence_payload),
        (TaskSpec, task_payload),
    ],
)
def test_contract_json_roundtrip_and_schema_interoperability(model, payload) -> None:
    item = model.model_validate_json(json.dumps(payload()))
    encoded = item.model_dump_json()
    assert model.model_validate_json(encoded) == item
    schema = model.model_json_schema()
    validator = validator_for(schema)
    validator.check_schema(schema)
    validator(schema).validate(json.loads(encoded))
    assert json.loads(encoded)["schema_version"] == "1.0"


@pytest.mark.parametrize(
    "model,payload",
    [
        (CapabilityDescriptor, capability_payload),
        (RoutingDecision, routing_payload),
        (RetrievalResult, evidence_payload),
        (TaskSpec, task_payload),
    ],
)
def test_contract_rejects_model_supplied_identity_and_unsupported_version(model, payload) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate({**payload(), "user_id": str(uuid4())})
    with pytest.raises(ValidationError, match="schema_version"):
        model.model_validate({**payload(), "schema_version": "2.0"})


def test_capability_is_metadata_with_conservative_side_effect_defaults() -> None:
    payload = capability_payload()
    del payload["side_effect"], payload["requires_approval"]
    item = CapabilityDescriptor(**payload)
    assert item.side_effect == "unknown" and item.requires_approval
    with pytest.raises(ValidationError, match="require approval"):
        CapabilityDescriptor(**{**payload, "side_effect": "write", "requires_approval": False})
    with pytest.raises(ValidationError, match="cannot require itself"):
        CapabilityDescriptor(**{**payload, "required_capability_ids": ["demo__calculate"]})


@pytest.mark.parametrize(
    "change",
    [
        {"selected_tool_ids": ["secret__delete"]},
        {"candidate_tool_ids": ["demo__calculate", "demo__calculate"]},
        {"selected_tool_ids": ["demo__calculate", "demo__calculate"]},
        {"route": "chat"},
        {"route": "unsupported_route"},
        {"probabilities": {"tool_task": 0.4, "chat": 0.4}},
        {"probabilities": {"tool_task": 0.1, "chat": 0.9}},
        {"probabilities": {}},
        {"probabilities": {"tool_task": float("nan")}},
        {"confidence": float("inf")},
        {"latency_ms": -1.0},
        {"input_tokens": True},
    ],
)
def test_routing_rejects_hallucinated_selection_and_invalid_measurements(change) -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(**{**routing_payload(), **change})


def test_rules_router_can_omit_unmeasured_probabilities_and_confidence() -> None:
    item = RoutingDecision(
        **{
            **routing_payload(),
            "provider": "rules",
            "probabilities": {},
            "confidence": None,
        }
    )
    assert item.confidence is None


def test_evidence_preserves_original_text_and_returns_only_identity_reference() -> None:
    payload = evidence_payload()
    result = RetrievalResult(**payload)
    assert result.text == payload["text"]
    assert result.reference().chunk_id == UUID(payload["chunk_id"])
    assert "text" not in result.reference().model_dump()
    assert result.trust == "untrusted"


@pytest.mark.parametrize(
    "change",
    [
        {"page_start": 0},
        {"page_end": 2},
        {"page_end": None},
        {"page_start": None, "page_end": None},
        {"start_offset": 10, "end_offset": 10},
        {"start_offset": 1},
        {"text": " \n "},
        {"score": float("nan")},
        {"trust": "trusted"},
    ],
)
def test_evidence_rejects_invalid_provenance_or_promoted_trust(change) -> None:
    with pytest.raises(ValidationError):
        RetrievalResult(**{**evidence_payload(), **change})


def test_non_pdf_evidence_can_use_section_and_half_open_offsets() -> None:
    result = RetrievalResult(
        **{
            **evidence_payload(),
            "page_start": None,
            "page_end": None,
            "section": "Introduction",
            "start_offset": 0,
            "end_offset": 20,
        }
    )
    assert result.start_offset == 0


@pytest.mark.parametrize(
    "change",
    [
        {"depends_on": ["read_a"]},
        {"depends_on": ["read_b", "read_b"]},
        {"allowed_capability_ids": []},
        {"acceptance_criteria": []},
        {"goal": "  "},
        {"agent_type": "arbitrary_shell_worker"},
        {"timeout_seconds": 0},
        {"output_schema": {"type": "array"}},
        {"output_schema": {"type": "object", "properties": {"summary": {"type": "invalid"}}}},
        {"budget": {"max_tokens": 0, "max_tool_calls": 0, "max_steps": 1}},
        {"budget": {"max_tokens": 100, "max_tool_calls": True, "max_steps": 1}},
        {
            "input_refs": [
                {"kind": "task_output", "reference_id": "result", "source_task_id": "read_b"}
            ]
        },
    ],
)
def test_task_rejects_invalid_dependencies_output_and_budgets(change) -> None:
    with pytest.raises(ValidationError):
        TaskSpec(**{**task_payload(), **change})


def test_task_can_reference_dependency_output_and_run_without_tools() -> None:
    task = TaskSpec(
        **{
            **task_payload(),
            "depends_on": ["read_b"],
            "allowed_capability_ids": [],
            "budget": {"max_tokens": 2000, "max_tool_calls": 0, "max_steps": 1},
            "input_refs": [
                {"kind": "task_output", "reference_id": "result", "source_task_id": "read_b"}
            ],
        }
    )
    assert task.input_refs[0].source_task_id in task.depends_on


def test_all_event_kinds_roundtrip_through_discriminated_union() -> None:
    common = event_envelope()
    events = [
        RoutingDecidedEvent(**common, decision=RoutingDecision(**routing_payload())),
        RetrievalCompletedEvent(**common, retrieval_config_version="hybrid-v1", duration_ms=20.0),
        PlanCreatedEvent(**common, plan_version=1, task_ids=["read_a"]),
        TaskStatusEvent(
            **common, plan_version=1, task_id="read_a", status="running", attempt_id=uuid4()
        ),
        RunStatusEvent(**common, status="completed"),
    ]
    for event in events:
        decoded = RUN_EVENT_ADAPTER.validate_json(event.model_dump_json())
        assert type(decoded) is type(event)
        assert decoded == event
        assert decoded.occurred_at == datetime(2026, 10, 18, 9, tzinfo=UTC)
    schema = RUN_EVENT_ADAPTER.json_schema()
    validator_for(schema).check_schema(schema)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "arbitrary_payload"},
        {"sequence": 0},
        {"sequence": True},
        {"occurred_at": "2026-10-18T09:00:00"},
        {"schema_version": "2.0"},
        {"task_id": "read_a"},
        {"attempt_id": str(uuid4())},
        {"user_id": str(uuid4())},
        {"chain_of_thought": "private reasoning"},
    ],
)
def test_event_rejects_unknown_kind_scope_and_untyped_payload(change) -> None:
    data = {**event_envelope(), "kind": "routing_decided", "decision": routing_payload(), **change}
    with pytest.raises(ValidationError):
        RUN_EVENT_ADAPTER.validate_json(json.dumps(data))


def test_task_events_require_attempt_and_error_correlation() -> None:
    data = {**event_envelope(), "plan_version": 1, "task_id": "read_a"}
    with pytest.raises(ValidationError, match="attempt_id"):
        TaskStatusEvent(**data, status="running")
    with pytest.raises(ValidationError, match="error_code"):
        TaskStatusEvent(**data, status="blocked")
    assert (
        TaskStatusEvent(**data, status="blocked", error_code="dependency_failed").attempt_id is None
    )
    with pytest.raises(ValidationError, match="Only failed"):
        RunStatusEvent(**event_envelope(), status="completed", error_code="provider_error")


def test_nested_models_are_revalidated_if_mutable_mapping_was_changed() -> None:
    decision = RoutingDecision(**routing_payload())
    decision.probabilities["tool_task"] = float("nan")
    with pytest.raises(ValidationError):
        RoutingDecidedEvent(**event_envelope(), decision=decision)
