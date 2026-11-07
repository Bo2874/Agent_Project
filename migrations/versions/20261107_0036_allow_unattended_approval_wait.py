"""Allow owner-controlled approval waits for unattended runs.

Revision ID: 20261107_0036
Revises: 20261107_0035
"""

import sqlalchemy as sa
from alembic import op

revision = "20261107_0036"
down_revision = "20261107_0035"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("agent_approvals", "expires_at", nullable=True)


def downgrade():
    count = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM agent_approvals WHERE expires_at IS NULL")
    )
    if count:
        raise RuntimeError(
            "Retained unattended approvals prevent downgrade; keep schema and disable automations"
        )
    op.alter_column("agent_approvals", "expires_at", nullable=False)
