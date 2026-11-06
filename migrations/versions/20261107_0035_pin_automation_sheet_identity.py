"""Pin stable Sheets tab identities in unattended grants.

Revision ID: 20261107_0035
Revises: 20261107_0034
"""

import sqlalchemy as sa
from alembic import op

revision = "20261107_0035"
down_revision = "20261107_0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("automation_grants", sa.Column("sheet_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("automation_grants", "sheet_id")
