"""conversations table

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 18:00:00.000000

Adds ``conversations``: one row per ask exchange, owned by the user who
asked the question. Row-Level Security mirrors the pattern from migration
0003: the ``explorai_app`` role sees only its own rows via ``app_user()``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('answered', 'insufficient_evidence')",
            name="conversation_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_created_at_id",
        "conversations",
        [sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
    )

    op.execute("ALTER TABLE conversations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY conversations_owner ON conversations "
        "USING (owner_id = app_user())"
    )
    op.execute(
        "GRANT SELECT, INSERT ON conversations TO explorai_app"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("REVOKE ALL ON conversations FROM explorai_app")
    op.execute("DROP POLICY conversations_owner ON conversations")
    op.execute("ALTER TABLE conversations DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_conversations_created_at_id", table_name="conversations")
    op.drop_table("conversations")
