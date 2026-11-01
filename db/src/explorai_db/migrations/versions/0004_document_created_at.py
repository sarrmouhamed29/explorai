"""document created_at

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 12:00:00.000000

Adds ``documents.created_at`` (needed by the keyset pagination of the API),
backfilled from the earliest matching artifact fetch time, and an index on
``(created_at DESC, id DESC)``. The backfill SQL is hand-written.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable first so existing rows can be backfilled, then NOT NULL.
    op.add_column('documents', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.execute(
        """
        UPDATE documents d SET created_at = COALESCE(
            (SELECT min(a.fetched_at) FROM raw_artifacts a
             WHERE a.source_id = d.source_id
               AND a.external_id = d.external_id
               AND a.owner_id IS NOT DISTINCT FROM d.owner_id),
            now())
        """
    )
    op.alter_column('documents', 'created_at', nullable=False)
    op.create_index('ix_documents_created_at_id', 'documents', [sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_documents_created_at_id', table_name='documents')
    op.drop_column('documents', 'created_at')
