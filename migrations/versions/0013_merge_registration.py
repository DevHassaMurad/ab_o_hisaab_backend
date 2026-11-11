"""Merge the donation/forecasting branch with the registration/document-verification branch.
No schema changes: it only joins the two migration heads so `upgrade head` works again."""

revision = "0013_merge_registration"
down_revision = ("0012_forecasting", "0005_document_verification")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
