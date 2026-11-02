"""pii columns on documents

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 12:00:00.000000

Adds ``documents.pii_action`` (owner's handling choice: "block" or "redact"),
``documents.pii_redacted`` and ``documents.pii_blocked`` (set by the pipeline).
All columns have server-side defaults so existing rows are backfilled without
a full-table write.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'documents',
        sa.Column(
            'pii_action',
            sa.String(16),
            server_default='block',
            nullable=False,
        ),
    )
    op.add_column(
        'documents',
        sa.Column(
            'pii_redacted',
            sa.Boolean(),
            server_default='false',
            nullable=False,
        ),
    )
    op.add_column(
        'documents',
        sa.Column(
            'pii_blocked',
            sa.Boolean(),
            server_default='false',
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'pii_blocked')
    op.drop_column('documents', 'pii_redacted')
    op.drop_column('documents', 'pii_action')
