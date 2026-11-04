from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from agent_core.context_budget import ContextTokenBudget
from agent_core.contracts.features import FeatureFlags
from agent_core.tools.email import SMTPConfig
from agent_core.tools.web_search import WebSearchConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    workspace_artifacts_enabled: bool = False
    google_connector_enabled: bool = False
    automations_enabled: bool = False
    v2_enabled: bool = False
    v2_plugins_enabled: bool = False
    v2_routing_enabled: bool = False
    v2_rag_enabled: bool = False
    v2_planning_enabled: bool = False
    v2_parallel_enabled: bool = False
    agent_parallel_max_workers: int = Field(default=3, ge=1, le=8)
    agent_parallel_workers_per_user: int = Field(default=3, ge=1, le=32)
    agent_parallel_workers_per_process: int = Field(default=8, ge=1, le=64)
    v2_redis_cache_enabled: bool = False
    v2_events_enabled: bool = False
    document_storage_path: Path = PROJECT_ROOT / "data/documents"
    document_max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    document_max_user_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    document_max_documents: int = Field(default=1000, ge=1, le=100000)
    document_max_unpacked_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    document_max_archive_entries: int = Field(default=2000, ge=2, le=10000)
    document_max_pages: int = Field(default=500, ge=1, le=2000)
    document_max_text_chars: int = Field(default=5_000_000, ge=1000, le=20_000_000)
    document_max_chunks: int = Field(default=20_000, ge=1, le=50_000)
    document_max_artifact_bytes: int = Field(
        default=128 * 1024 * 1024, ge=1024, le=512 * 1024 * 1024
    )
    document_process_timeout_seconds: float = Field(default=1800, ge=30, le=3600)
    document_process_memory_mb: int = Field(default=4096, ge=512, le=32768)
    document_libreoffice_path: str = "soffice"
    document_model_cache_path: Path = PROJECT_ROOT / "data/models"
    document_embedding_revision: str = Field(default="main", min_length=1, max_length=160)
    document_embedding_batch_size: int = Field(default=4, ge=1, le=32)
    document_embedding_timeout_seconds: float = Field(default=600, ge=30, le=3600)
    document_embedding_memory_mb: int = Field(default=4096, ge=1024, le=32768)
    document_reranker_revision: str = Field(default="main", min_length=1, max_length=160)
    document_reranker_batch_size: int = Field(default=1, ge=1, le=4)
    document_reranker_timeout_seconds: float = Field(default=600, ge=30, le=3600)
    document_reranker_memory_mb: int = Field(default=4096, ge=1024, le=32768)
    document_rewrite_timeout_seconds: float = Field(default=30, ge=5, le=120)
    document_qa_timeout_seconds: float = Field(default=600, ge=5, le=3600)
    document_rewrite_history_messages: int = Field(default=8, ge=1, le=20)
    document_rewrite_history_tokens: int = Field(default=2000, ge=100, le=8000)
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "personal_agent_db"
    db_user: str = "postgres"
    db_password: SecretStr
    langgraph_checkpoint_pool_min_size: int = Field(default=1, ge=1, le=20)
    langgraph_checkpoint_pool_max_size: int = Field(default=5, ge=1, le=100)
    langgraph_checkpoint_auto_setup: bool = True

    auth_secret_key: SecretStr = Field(min_length=32)
    auth_token_expire_minutes: int = Field(default=60, ge=5, le=1440)
    auth_cookie_secure: bool = False

    openrouter_api_key: SecretStr | None = None
    web_search_enabled: bool = False
    web_search_model: str = "openai/gpt-5.4-mini"
    web_search_timeout_seconds: float = Field(default=60, gt=0, le=90)
    web_search_max_output_tokens: int = Field(default=2000, ge=256, le=8000)
    jev_base_url: str = "https://openrouter.ai/api"
    jev_model: str = "typesafe/jev-1.14"
    jev_timeout_seconds: float = Field(default=15, gt=0, le=120)
    jev_total_timeout_seconds: float = Field(default=20, gt=0, le=180)
    routing_mode: Literal["active", "shadow"] = "active"
    routing_query_max_state_chars: int = Field(default=32768, ge=128, le=131072)
    routing_query_max_payload_bytes: int = Field(default=131072, ge=512, le=524288)
    routing_fallback_enabled: bool = True
    routing_primary_timeout_seconds: float = Field(default=8, gt=0, le=120)
    routing_max_provider_calls: int = Field(default=12, ge=1, le=100)
    routing_max_reserved_tokens: int = Field(default=20000, ge=256, le=1000000)
    routing_min_probability: float = Field(default=0.6, ge=0, le=1)
    routing_min_margin: float = Field(default=0.1, ge=0, le=1)
    routing_breaker_threshold: int = Field(default=3, ge=1, le=100)
    routing_breaker_cooldown_seconds: float = Field(default=30, gt=0, le=3600)
    routing_allow_external: bool = True
    routing_send_observations: bool = True
    routing_redact_pii: bool = True
    routing_max_state_chars: int = Field(default=32768, ge=128, le=32768)
    routing_max_payload_bytes: int = Field(default=131072, ge=512, le=131072)

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_thinking: bool = False
    deepseek_max_tokens: int = Field(default=104_857, ge=128, le=393_216)
    deepseek_max_retries: int = Field(default=2, ge=0, le=5)
    deepseek_retry_backoff_seconds: float = Field(default=1.0, gt=0, le=10)
    deepseek_cache_hit_price_per_million_usd: float = Field(default=0.006, ge=0)
    deepseek_cache_miss_price_per_million_usd: float = Field(default=0.30, ge=0)
    deepseek_output_price_per_million_usd: float = Field(default=1.20, ge=0)
    langsmith_tracing_enabled: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "personal-ai-agent"
    langsmith_sampling_rate: float = Field(default=1.0, ge=0, le=1)
    operational_error_retention_days: int = Field(default=30, ge=1, le=3650)
    tool_payload_retention_days: int = Field(default=30, ge=1, le=3650)
    agent_run_retention_days: int = Field(default=90, ge=1, le=3650)
    background_job_retention_days: int = Field(default=30, ge=1, le=3650)
    audit_log_retention_days: int = Field(default=180, ge=1, le=3650)
    admin_alert_failure_rate: float = Field(default=0.10, ge=0, le=1)
    admin_alert_cache_hit_rate: float = Field(default=0.20, ge=0, le=1)
    admin_alert_model_p95_ms: int = Field(default=10_000, ge=1, le=3_600_000)
    admin_alert_stuck_run_minutes: int = Field(default=10, ge=1, le=10_080)
    admin_alert_min_sample_count: int = Field(default=5, ge=1, le=100_000)
    admin_alert_min_cache_tokens: int = Field(default=1_000, ge=1, le=1_000_000_000)
    chat_history_limit: int = Field(default=20, ge=2, le=100)
    history_compression_batch_size: int = Field(default=10, ge=2, le=100)
    history_summary_merge_factor: int = Field(default=4, ge=2, le=20)
    history_summary_timeout_seconds: float = Field(default=180, gt=0, le=3600)
    memory_extraction_enabled: bool = True
    memory_extraction_min_confidence: float = Field(default=0.6, ge=0, le=1)
    memory_extraction_timeout_seconds: float = Field(default=20, gt=0, le=180)
    memory_retrieval_min_confidence: float = Field(default=0.6, ge=0, le=1)
    memory_retrieval_max_items: int = Field(default=20, ge=1, le=100)
    memory_retrieval_candidate_limit: int = Field(default=100, ge=1, le=1000)
    memory_retention_normal_days: int = Field(default=365, ge=1, le=3650)
    memory_retention_personal_days: int = Field(default=180, ge=1, le=3650)
    memory_retention_sensitive_days: int = Field(default=30, ge=1, le=3650)
    agent_max_steps: int | None = Field(default=None, ge=1)
    agent_react_enabled: bool = False
    agent_web_search_estimated_tokens: int = Field(default=10_000, ge=1_000, le=100_000)
    # The token ceiling is independent from optional step/time/repetition caps.
    agent_document_max_total_tokens: int | None = Field(default=None, ge=128, le=100_000_000)
    agent_max_total_tokens: int | None = Field(default=None, ge=128, le=100_000_000)
    agent_max_duration_seconds: float | None = Field(default=None, gt=0)
    agent_max_identical_tool_calls: int | None = Field(default=None, ge=1)
    agent_max_repeated_observations: int | None = Field(default=None, ge=1)
    agent_requests_per_minute: int = Field(default=10, ge=1, le=10_000)
    mcp_demo_enabled: bool = True
    mcp_stdio_config_path: Path = PROJECT_ROOT / "plugins/stdio/servers.json"
    mcp_http_config_path: Path = PROJECT_ROOT / "plugins/http/servers.json"
    plugin_packages_config_path: Path = PROJECT_ROOT / "plugins/packages.json"
    mcp_credential_encryption_keys: SecretStr | None = None
    python_sandbox_enabled: bool = True
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Literal["ssl", "starttls"] = "ssl"
    smtp_username: str = ""
    smtp_password: SecretStr = Field(default=SecretStr(""), repr=False)
    smtp_from_email: str = ""
    smtp_timeout_seconds: float = Field(default=20, gt=0, le=60)
    background_queue_name: str = Field(default="default", pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    background_worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    background_worker_heartbeat_path: Path | None = None
    background_worker_heartbeat_max_age_seconds: float = Field(default=15, ge=5, le=300)
    background_job_lease_seconds: float = Field(default=30.0, ge=1, le=3600)
    background_job_control_poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    background_job_retry_base_seconds: float = Field(default=1.0, ge=0, le=3600)
    background_job_retry_max_seconds: float = Field(default=60.0, ge=0, le=3600)
    tool_timeout_seconds: float = Field(default=10, gt=0, le=300)
    tool_max_retries: int = Field(default=1, ge=0, le=5)
    tool_retry_backoff_seconds: float = Field(default=0.2, ge=0, le=10)
    tool_allowlist: str = (
        "demo__get_server_time,demo__calculate,demo__count_text,"
        "demo__convert_temperature,demo__simulate_sensitive_action,"
        "demo__get_admin_runtime_info,run_python_code,send_email,search_web,"
        "search_documents,summarize_documents"
    )
    tool_approval_timeout_seconds: float = Field(default=300, gt=0, le=3600)
    tool_lazy_load_threshold: int = Field(default=4, ge=1, le=1000)
    tool_max_loaded: int = Field(default=4, ge=1, le=100)
    skill_selection_min_score: int = Field(default=20, ge=1, le=1000)
    skill_selection_ambiguity_margin: int = Field(default=5, ge=0, le=1000)
    context_window_tokens: int = Field(default=1_048_576, ge=1_000, le=1_048_576)
    context_system_tools_percent: int = Field(default=5, ge=0, le=100)
    context_memory_history_percent: int = Field(default=65, ge=0, le=100)
    context_rag_tool_results_percent: int = Field(default=15, ge=0, le=100)
    context_output_percent: int = Field(default=10, ge=1, le=100)
    context_safety_percent: int = Field(default=5, ge=0, le=100)

    @property
    def context_budget(self) -> ContextTokenBudget:
        return ContextTokenBudget.from_percentages(
            self.context_window_tokens,
            system_tools=self.context_system_tools_percent,
            memory_history=self.context_memory_history_percent,
            rag_tool_results=self.context_rag_tool_results_percent,
            output=self.context_output_percent,
            safety=self.context_safety_percent,
        )

    @property
    def context_rag_token_budget(self) -> int:
        return self.context_budget.rag_tool_result_tokens

    @property
    def agent_run_token_budget(self) -> int:
        budget = self.context_budget
        return self.agent_max_total_tokens or 8 * (
            budget.max_input_tokens + budget.output_tokens + 64
        )

    @property
    def agent_document_run_token_budget(self) -> int:
        return self.agent_document_max_total_tokens or self.agent_run_token_budget

    @field_validator(
        "agent_max_steps",
        "agent_max_duration_seconds",
        "agent_max_identical_tool_calls",
        "agent_max_repeated_observations",
        mode="before",
    )
    @classmethod
    def normalize_optional_agent_limits(cls, value):
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "unlimited"}:
            return None
        return value

    @field_validator("background_worker_heartbeat_path", mode="before")
    @classmethod
    def normalize_heartbeat_path(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_context_budgets(self) -> "Settings":
        _ = self.v2_features  # Validate feature dependencies at configuration load time.
        if self.v2_features.is_enabled("planning") and not self.agent_react_enabled:
            raise ValueError("V2 planning requires AGENT_REACT_ENABLED")
        _ = self.email_config
        if self.document_max_user_bytes < self.document_max_file_bytes:
            raise ValueError("DOCUMENT_MAX_USER_BYTES không được nhỏ hơn DOCUMENT_MAX_FILE_BYTES")
        if self.langgraph_checkpoint_pool_min_size > self.langgraph_checkpoint_pool_max_size:
            raise ValueError(
                "LANGGRAPH_CHECKPOINT_POOL_MIN_SIZE không được vượt "
                "LANGGRAPH_CHECKPOINT_POOL_MAX_SIZE"
            )
        if self.background_job_control_poll_seconds > self.background_job_lease_seconds / 2:
            raise ValueError("BACKGROUND_JOB_CONTROL_POLL_SECONDS không được vượt một nửa lease")
        if self.background_worker_heartbeat_path is not None:
            if self.background_worker_heartbeat_max_age_seconds <= max(
                self.background_worker_poll_seconds, self.background_job_control_poll_seconds
            ):
                raise ValueError("Worker heartbeat max age must exceed its polling intervals")
        if self.background_job_retry_base_seconds > self.background_job_retry_max_seconds:
            raise ValueError("BACKGROUND_JOB_RETRY_BASE_SECONDS không được vượt retry max")
        if not (
            self.memory_retention_sensitive_days
            <= self.memory_retention_personal_days
            <= self.memory_retention_normal_days
        ):
            raise ValueError("Memory retention phải theo thứ tự sensitive <= personal <= normal")
        if self.tool_max_loaded > self.tool_lazy_load_threshold:
            raise ValueError("TOOL_MAX_LOADED không được vượt TOOL_LAZY_LOAD_THRESHOLD")
        if self.memory_retrieval_max_items > self.memory_retrieval_candidate_limit:
            raise ValueError(
                "MEMORY_RETRIEVAL_MAX_ITEMS không được vượt MEMORY_RETRIEVAL_CANDIDATE_LIMIT"
            )
        if self.deepseek_max_tokens > self.context_budget.output_tokens:
            raise ValueError("DEEPSEEK_MAX_TOKENS vượt Output Reservation của context")
        return self

    @property
    def web_search_config(self) -> WebSearchConfig | None:
        if not self.web_search_enabled or not self.openrouter_api_key:
            return None
        return WebSearchConfig(
            api_key=self.openrouter_api_key,
            model=self.web_search_model,
            timeout_seconds=self.web_search_timeout_seconds,
            max_output_tokens=self.web_search_max_output_tokens,
        )

    @property
    def email_config(self) -> SMTPConfig | None:
        if not self.email_enabled:
            return None
        return SMTPConfig(
            host=self.smtp_host,
            port=self.smtp_port,
            security=self.smtp_security,
            username=self.smtp_username,
            password=self.smtp_password,
            from_email=self.smtp_from_email or self.smtp_username,
            timeout_seconds=self.smtp_timeout_seconds,
        )

    @property
    def v2_features(self) -> FeatureFlags:
        """Return a validated snapshot; consumers must also check component availability."""
        return FeatureFlags(
            enabled=self.v2_enabled,
            plugins=self.v2_plugins_enabled,
            routing=self.v2_routing_enabled,
            rag=self.v2_rag_enabled,
            planning=self.v2_planning_enabled,
            parallel=self.v2_parallel_enabled,
            redis_cache=self.v2_redis_cache_enabled,
            events=self.v2_events_enabled,
        )

    @property
    def tool_allowlist_names(self) -> frozenset[str]:
        return frozenset(name.strip() for name in self.tool_allowlist.split(",") if name.strip())

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def migration_database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def langgraph_checkpoint_database_uri(self) -> str:
        return URL.create(
            drivername="postgresql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
