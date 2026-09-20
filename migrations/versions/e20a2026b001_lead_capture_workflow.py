"""Add prospect identity, lead conversion, actual close dates and reminder completion.

Existing rows remain intact. Actual close dates are deliberately not inferred
from the old expected-date field. Historical closed deals require a date review.
"""
from alembic import op
import sqlalchemy as sa

revision = "e20a2026b001"
down_revision = "cdf49c61fe01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("prospect_name", sa.String(120), nullable=True))
        batch.add_column(sa.Column("email", sa.String(120), nullable=True))
        batch.add_column(sa.Column("phone", sa.String(30), nullable=True))
        batch.add_column(sa.Column("company_name", sa.String(150), nullable=True))
        batch.add_column(sa.Column("contact_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_leads_contact_id_contacts", "contacts", ["contact_id"], ["id"])
    with op.batch_alter_table("deals") as batch:
        batch.add_column(sa.Column("actual_close_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("lead_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_deals_lead_id_leads", "leads", ["lead_id"], ["id"])
        batch.create_unique_constraint("uq_deals_lead_id", ["lead_id"])
    with op.batch_alter_table("reminders") as batch:
        batch.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))


def downgrade():
    # Downgrading discards new fields. Back up before explicitly requesting it.
    with op.batch_alter_table("reminders") as batch:
        batch.drop_column("completed_at")
    with op.batch_alter_table("deals") as batch:
        batch.drop_constraint("uq_deals_lead_id", type_="unique")
        batch.drop_constraint("fk_deals_lead_id_leads", type_="foreignkey")
        batch.drop_column("lead_id")
        batch.drop_column("actual_close_date")
    with op.batch_alter_table("leads") as batch:
        batch.drop_constraint("fk_leads_contact_id_contacts", type_="foreignkey")
        for name in ("contact_id", "company_name", "phone", "email", "prospect_name"):
            batch.drop_column(name)
