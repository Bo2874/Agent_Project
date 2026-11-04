"""Retain independent artifact copies when their RAG source is deleted."""

from alembic import op

revision = "20261104_0032"
down_revision = "20261104_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_artifact_source_document", "artifact_versions", type_="foreignkey")
    op.create_foreign_key(
        "fk_artifact_source_document",
        "artifact_versions",
        "document_versions",
        ["source_document_version_id", "source_document_id", "owner_user_id"],
        ["id", "document_id", "owner_user_id"],
        ondelete="SET NULL (source_document_version_id, source_document_id)",
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION artifact_version_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (to_jsonb(NEW) - ARRAY['source_run_id','source_document_id',
                                  'source_document_version_id'])
             IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['source_run_id','source_document_id',
                                  'source_document_version_id'])
             OR (NEW.source_run_id IS DISTINCT FROM OLD.source_run_id
                 AND NEW.source_run_id IS NOT NULL)
             OR (NEW.source_document_id IS DISTINCT FROM OLD.source_document_id
                 AND NEW.source_document_id IS NOT NULL)
             OR (NEW.source_document_version_id IS DISTINCT FROM OLD.source_document_version_id
                 AND NEW.source_document_version_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Published artifact versions are immutable';
          END IF;
          RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    op.drop_constraint("fk_artifact_source_document", "artifact_versions", type_="foreignkey")
    op.create_foreign_key(
        "fk_artifact_source_document",
        "artifact_versions",
        "document_versions",
        ["source_document_version_id", "source_document_id", "owner_user_id"],
        ["id", "document_id", "owner_user_id"],
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION artifact_version_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (to_jsonb(NEW) - 'source_run_id') IS DISTINCT FROM
             (to_jsonb(OLD) - 'source_run_id') OR
             (NEW.source_run_id IS DISTINCT FROM OLD.source_run_id AND
              NEW.source_run_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Published artifact versions are immutable';
          END IF;
          RETURN NEW;
        END $$
    """)
