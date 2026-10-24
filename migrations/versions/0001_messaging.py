"""Service-owned outbox and deduplication infrastructure."""
from alembic import op
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE TABLE processed_event (consumer_name varchar(120) NOT NULL, event_id uuid NOT NULL, processed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (consumer_name, event_id))")
    op.execute("CREATE TABLE outbox_event (event_id uuid PRIMARY KEY, aggregate_id uuid NOT NULL, event_type varchar(120) NOT NULL, event_version integer NOT NULL CHECK (event_version > 0), correlation_id uuid NOT NULL, occurred_at timestamptz NOT NULL, payload jsonb NOT NULL, published_at timestamptz, attempts integer NOT NULL DEFAULT 0)")
    op.execute("CREATE INDEX outbox_unpublished ON outbox_event(occurred_at) WHERE published_at IS NULL")

def downgrade():
    op.drop_table("outbox_event")
    op.drop_table("processed_event")
