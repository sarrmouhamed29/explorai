"""chunks

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01 18:00:00.000000

Adds ``chunks`` (retrieval units cut from the sections of a version),
``chunk_index_entries`` (which chunk is in which vector index, named after the
embedding model and dimension) and ``document_versions.chunked_at``. The tables are written to model
autogenerate's output (no database was reachable to run it); the SQL after them
is hand-written, as in 0003: grants, Row-Level Security and policies.

The API role only reads. The indexing pipeline writes as the table owner (RLS
is enabled without ``FORCE``, so the owner bypasses it), and the ``ON DELETE
CASCADE`` actions run as the owner too: deleting a document or a version
removes its chunks and entries without any ``DELETE`` grant to the API role.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RLS_TABLES = ("chunks", "chunk_index_entries")
GRANTS = {
    "chunks": "SELECT",
    "chunk_index_entries": "SELECT",
}

# (table, policy name, command, clause) for the explorai_app role. Each subquery
# is itself filtered by the policy of the table it reads, so a chunk is visible
# exactly when its version is (which follows its document), and an entry exactly
# when its chunk is. Column names are qualified: an unqualified ``version_id``
# inside the subquery would resolve to the inner table first if it had one.
POLICIES = [
    (
        "chunks",
        "chunks_select",
        "SELECT",
        "USING (EXISTS (SELECT 1 FROM document_versions v"
        " WHERE v.id = chunks.version_id))",
    ),
    (
        "chunk_index_entries",
        "chunk_index_entries_select",
        "SELECT",
        "USING (EXISTS (SELECT 1 FROM chunks c"
        " WHERE c.id = chunk_index_entries.chunk_id))",
    ),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('chunks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('version_id', sa.Uuid(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('heading', sa.Text(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('text_hash', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['version_id'], ['document_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'ordinal')
    )
    op.create_table('chunk_index_entries',
    sa.Column('chunk_id', sa.Uuid(), nullable=False),
    sa.Column('index_name', sa.String(length=128), nullable=False),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['chunk_id'], ['chunks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('chunk_id', 'index_name')
    )
    op.add_column('document_versions', sa.Column('chunked_at', sa.DateTime(timezone=True), nullable=True))
    # Serves the eligibility lookup (latest ok version per document), which runs
    # on every question; PostgreSQL does not index foreign key columns itself.
    op.create_index('ix_document_versions_document_status_created', 'document_versions', ['document_id', 'status', 'created_at', 'id'], unique=False)

    # The role and app_user() exist since 0003.
    for table, privileges in GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO explorai_app")
    # ENABLE without FORCE: the owner (migrations, pipeline) bypasses RLS.
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for table, name, command, clause in POLICIES:
        op.execute(f"CREATE POLICY {name} ON {table} FOR {command} TO explorai_app {clause}")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_document_versions_document_status_created', table_name='document_versions')
    for table, name, _command, _clause in POLICIES:
        op.execute(f"DROP POLICY {name} ON {table}")
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM explorai_app")

    op.drop_column('document_versions', 'chunked_at')
    op.drop_table('chunk_index_entries')
    op.drop_table('chunks')
