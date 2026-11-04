"""Allow history summaries longer than their source when context budget permits."""

from alembic import op

revision = "20261104_0030"
down_revision = "20261027_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_conversation_summaries_compressed_chars", "conversation_summaries", type_="check"
    )


def downgrade() -> None:
    # PostgreSQL refuses this downgrade if longer summaries exist; never delete
    # or truncate conversation data to restore the former policy automatically.
    op.create_check_constraint(
        "ck_conversation_summaries_compressed_chars",
        "conversation_summaries",
        "source_char_count >= summary_char_count",
    )
