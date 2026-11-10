"""Additive append-only M4 evidence. Install only on a newly owned generated database."""
from importlib.resources import files
from alembic import op
from sqlalchemy import text
import os
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    from agrosphere_ai.adapters.ownership import Guard
    guard = Guard(os.environ["AI_GENERATED_MANIFEST"], os.environ["AI_GENERATED_MANIFEST_SHA256"], os.environ["AI_DATABASE_URL"])
    row = op.get_bind().execute(text("SELECT current_user,shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")).one()
    if row[0] != guard.value["migrationUser"] or row[1] != guard.value["dbMarker"]:
        raise PermissionError("Migration target is not the owned M4 fixture")
    op.execute(files("agrosphere_ai").joinpath("sql/m4.sql").read_text())


def downgrade():
    raise RuntimeError("M4 does not authorize destructive rollback")
