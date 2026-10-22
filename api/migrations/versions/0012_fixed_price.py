"""fixed-price billing: invoices recorded from elsewhere

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22 03:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('invoices') as b:
        b.add_column(sa.Column('external', sa.Boolean(), server_default=sa.false(), nullable=False))
        b.add_column(sa.Column('external_number', sa.String(length=60), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('invoices') as b:
        b.drop_column('external_number')
        b.drop_column('external')
