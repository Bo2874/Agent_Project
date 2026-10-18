import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (
        CheckConstraint(
            "metric IN ('agent_request', 'agent_run', 'llm_token', 'tool_call')",
            name="ck_usage_counters_metric",
        ),
        CheckConstraint("value >= 0", name="ck_usage_counters_value"),
        CheckConstraint("window_seconds > 0", name="ck_usage_counters_window_seconds"),
        Index(
            "ix_usage_counters_user_metric_window",
            "user_id",
            "metric",
            "window_start",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
