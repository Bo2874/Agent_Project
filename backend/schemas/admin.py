from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AdminTokenMetrics(BaseModel):
    total: int
    prompt: int
    completion: int
    cached_input: int
    cache_miss_input: int


class AdminActivityMetrics(BaseModel):
    agent_runs: int
    model_turns: int
    model_calls: int
    tool_calls: int


class AdminLatencyMetrics(BaseModel):
    average_model_ms: int | None
    model_p50_ms: int | None
    model_p95_ms: int | None
    average_tool_ms: int | None
    tool_p50_ms: int | None
    tool_p95_ms: int | None


class AdminCacheMetrics(BaseModel):
    hit_tokens: int
    miss_tokens: int
    hit_rate: float | None


class AdminCostMetrics(BaseModel):
    currency: str = "USD"
    estimated_total: float
    cached_input: float
    cache_miss_input: float
    output: float


class AdminErrorMetrics(BaseModel):
    total: int
    agent_runs: int
    model_calls: int
    tool_calls: int


class AdminToolMetrics(BaseModel):
    completed: int
    failed: int
    denied: int
    waiting_approval: int


class AdminRecentModelCall(BaseModel):
    model_name: str
    status: str
    total_tokens: int
    latency_ms: int | None
    prompt_version: str | None
    created_at: datetime


class AdminRecentToolCall(BaseModel):
    tool_name: str
    status: str
    duration_ms: int | None
    error_code: str | None
    created_at: datetime


class AdminRecentError(BaseModel):
    source: str
    code: str
    message: str = Field(max_length=500)
    created_at: datetime


class AdminAlert(BaseModel):
    code: str
    severity: Literal["warning", "critical"]
    title: str
    detail: str


class AdminOverviewResponse(BaseModel):
    window_days: int
    from_time: datetime
    to_time: datetime
    tokens: AdminTokenMetrics
    cache: AdminCacheMetrics
    cost: AdminCostMetrics
    activity: AdminActivityMetrics
    latency: AdminLatencyMetrics
    errors: AdminErrorMetrics
    tools: AdminToolMetrics
    run_statuses: dict[str, int]
    recent_model_calls: list[AdminRecentModelCall]
    recent_tool_calls: list[AdminRecentToolCall]
    recent_errors: list[AdminRecentError]
    alerts: list[AdminAlert]


class AdminEvalCategoryMetrics(BaseModel):
    attempted: int
    passed: int
    failed: int
    errors: int
    pass_rate: float | None


class AdminEvalRunSummary(BaseModel):
    id: UUID
    status: str
    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    provider: str
    model_name: str
    prompt_version: str
    prompt_fingerprint: str
    judge_model_name: str
    selected_case_ids: list[str]
    total_cases: int
    completed_cases: int
    passed_cases: int
    failed_cases: int
    error_cases: int
    pass_rate: float | None
    tool_success_rate: float | None
    categories: dict[str, AdminEvalCategoryMetrics]
    failure_categories: dict[str, int]
    created_at: datetime
    finished_at: datetime | None


class AdminEvalRunsResponse(BaseModel):
    runs: list[AdminEvalRunSummary]


class AdminEvalComparisonDelta(BaseModel):
    pass_rate: float | None
    tool_success_rate: float | None
    categories: dict[str, float | None]
    failure_categories: dict[str, int]


class AdminEvalComparisonResponse(BaseModel):
    baseline: AdminEvalRunSummary
    candidate: AdminEvalRunSummary
    same_dataset: bool
    same_case_selection: bool
    comparable: bool
    warnings: list[str]
    delta: AdminEvalComparisonDelta


class AdminAgentRunSummary(BaseModel):
    id: UUID
    status: str
    current_step: int
    stop_reason: str | None
    duration_ms: int | None
    created_at: datetime
    finished_at: datetime | None


class AdminAgentRunsResponse(BaseModel):
    runs: list[AdminAgentRunSummary]


class AdminAgentRunModelCall(BaseModel):
    id: UUID
    model_name: str
    status: str
    prompt_version: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_input_tokens: int
    cache_miss_input_tokens: int
    cache_hit_rate: float | None
    estimated_cost_usd: float
    latency_ms: int | None


class AdminAgentRunToolCall(BaseModel):
    id: UUID
    tool_name: str
    status: str
    duration_ms: int | None
    error_code: str | None
    approval_decision: str | None


class AdminAgentRunStep(BaseModel):
    id: UUID
    step_number: int
    attempt_number: int
    status: str
    duration_ms: int | None
    model_call: AdminAgentRunModelCall | None
    tool_calls: list[AdminAgentRunToolCall]


class AdminAgentRunDetail(BaseModel):
    id: UUID
    conversation_id: UUID
    status: str
    current_step: int
    stop_reason: str | None
    duration_ms: int | None
    resume_count: int
    context_total_tokens: int | None
    context_budget_tokens: int | None
    context_trimmed_messages: int
    tool_selection_mode: str | None
    available_tool_count: int | None
    loaded_tool_count: int | None
    total_tokens: int
    cache_hit_rate: float | None
    estimated_cost_usd: float
    created_at: datetime
    finished_at: datetime | None
    steps: list[AdminAgentRunStep]


class AdminAuditLogItem(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    metadata: dict[str, object]
    created_at: datetime


class AdminAuditLogsResponse(BaseModel):
    logs: list[AdminAuditLogItem]
