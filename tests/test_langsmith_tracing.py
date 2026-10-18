from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

from agent_core.graph import stream_agent_graph
from agent_core.llm.types import LLMChunk, ModelMessage
from agent_core.tools import ToolContext, ToolRegistry
from agent_core.tracing import AgentTraceConfig
from backend.core.redaction import REDACTED
from backend.services.langsmith_tracing import (
    LangSmithTracingService,
    anonymous_trace_id,
    redact_langsmith_payload,
)


class FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.flushed = False
        self.closed = False

    def flush(self) -> None:
        self.flushed = True

    def close(self) -> None:
        self.closed = True


class FakeTracer(BaseCallbackHandler):
    def __init__(self, *, project_name: str, client: FakeClient) -> None:
        self.project_name = project_name
        self.client = client
        self.waited = False

    def wait_for_futures(self) -> None:
        self.waited = True


def _service(*, enabled: bool = True, api_key: str = "lsv2_test") -> LangSmithTracingService:
    return LangSmithTracingService(
        enabled=enabled,
        api_key=api_key,
        endpoint="https://api.smith.langchain.com",
        project="personal-agent-test",
        sampling_rate=1,
        hash_salt="a-test-secret-with-at-least-32-characters",
        app_env="test",
        client_factory=FakeClient,
        tracer_factory=FakeTracer,
    )


def test_trace_config_merges_callbacks_tags_and_metadata() -> None:
    callback = FakeTracer(project_name="test", client=FakeClient())
    trace = AgentTraceConfig(
        callbacks=(callback,),
        tags=("safe-tag",),
        metadata={"model": "deepseek-flash"},
    )

    config = trace.apply(
        {
            "configurable": {"thread_id": "internal-thread"},
            "tags": ["existing"],
            "metadata": {"env": "test"},
        }
    )

    assert config["callbacks"] == [callback]
    assert config["tags"] == ["existing", "safe-tag"]
    assert config["metadata"] == {
        "env": "test",
        "model": "deepseek-flash",
    }
    assert config["run_name"] == "personal-agent"
    assert config["configurable"] == {"thread_id": "internal-thread"}


def test_langsmith_payload_redacts_identifiers_secrets_and_pii() -> None:
    payload = {
        "thread_id": "agent-run-raw-uuid",
        "nested": {
            "user_id": "raw-user-uuid",
            "api_key": "secret-value",
            "message": (
                "email owner@example.com password=hunter2 run=123e4567-e89b-42d3-a456-426614174000"
            ),
        },
    }

    redacted = redact_langsmith_payload(payload)

    assert redacted["thread_id"] == REDACTED
    assert redacted["nested"]["user_id"] == REDACTED
    assert redacted["nested"]["api_key"] == REDACTED
    assert "owner@example.com" not in redacted["nested"]["message"]
    assert "hunter2" not in redacted["nested"]["message"]
    assert "123e4567-e89b-42d3-a456-426614174000" not in redacted["nested"]["message"]


def test_trace_tags_use_stable_scoped_hashes_without_raw_ids() -> None:
    service = _service()
    run_id = uuid4()
    user_id = uuid4()

    first = service.trace_config(
        run_id=run_id,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
    )
    second = service.trace_config(
        run_id=run_id,
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
    )

    assert first is not None
    assert second is not None
    assert first.tags == second.tags
    serialized_tags = " ".join(first.tags)
    assert str(run_id) not in serialized_tags
    assert str(user_id) not in serialized_tags
    assert any(tag.startswith("run:") for tag in first.tags)
    assert any(tag.startswith("user:") for tag in first.tags)
    assert anonymous_trace_id(
        run_id,
        salt="a-test-secret-with-at-least-32-characters",
        namespace="agent-run",
    ) != anonymous_trace_id(
        run_id,
        salt="a-test-secret-with-at-least-32-characters",
        namespace="user",
    )


def test_tracing_is_disabled_without_explicit_enable_and_api_key() -> None:
    disabled = _service(enabled=False)
    missing_key = _service(enabled=True, api_key="")

    for service in (disabled, missing_key):
        assert service.enabled is False
        assert (
            service.trace_config(
                run_id=uuid4(),
                user_id=uuid4(),
                provider="deepseek",
                model="deepseek-flash",
            )
            is None
        )


async def test_langsmith_client_hides_payloads_and_flushes_on_shutdown() -> None:
    service = _service()
    trace = service.trace_config(
        run_id=uuid4(),
        user_id=uuid4(),
        provider="deepseek",
        model="deepseek-flash",
    )
    assert trace is not None
    tracer = trace.callbacks[0]
    assert isinstance(tracer, FakeTracer)
    client = tracer.client

    assert client.kwargs["hide_inputs"] is True
    assert client.kwargs["hide_outputs"] is True
    assert callable(client.kwargs["anonymizer"])
    assert callable(client.kwargs["hide_metadata"])
    assert client.kwargs["omit_traced_runtime_info"] is True

    await service.close()

    assert tracer.waited is True
    assert client.flushed is True
    assert client.closed is True


async def test_agent_graph_uses_trace_callback_without_exposing_ids_in_tags() -> None:
    class RecordingCallback(BaseCallbackHandler):
        def __init__(self) -> None:
            self.names: list[str] = []

        def on_chain_start(
            self,
            _serialized: dict[str, Any],
            _inputs: dict[str, Any],
            *,
            run_id: Any,
            name: str | None = None,
            **_kwargs: Any,
        ) -> None:
            del run_id
            self.names.append(name or "unknown")

    class FinalModel:
        provider = "fake"
        model = "fake-traced-model"

        async def stream(
            self,
            _messages: list[ModelMessage],
            tools: list[dict[str, Any]] | None = None,
            user_id: str | None = None,
        ) -> AsyncIterator[LLMChunk]:
            del tools
            assert user_id is not None
            yield LLMChunk(content="Đã trace.", finish_reason="stop")

        async def aclose(self) -> None:
            return None

    callback = RecordingCallback()
    user_id = uuid4()
    run_id = uuid4()
    trace_config = AgentTraceConfig(
        callbacks=(callback,),
        tags=("run:hashed", "user:hashed"),
        metadata={"payload_policy": "inputs_outputs_hidden"},
    )
    items = [
        item
        async for item in stream_agent_graph(
            model=FinalModel(),
            messages=[{"role": "user", "content": "Thông tin riêng tư"}],
            registry=ToolRegistry(),
            context=ToolContext(
                user_id=user_id,
                conversation_id=uuid4(),
                user_role="user",
                run_id=run_id,
            ),
            trace_config=trace_config,
        )
    ]

    assert callback.names
    assert any(isinstance(item, LLMChunk) and item.content == "Đã trace." for item in items)
