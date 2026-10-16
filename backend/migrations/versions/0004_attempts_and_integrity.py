"""Processing attempts, lease tokens, uniqueness and value constraints.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

POKEMON_CHECKS = [
    ("ck_pokemon_cp", "cp IS NULL OR (cp BETWEEN 10 AND 6000)"),
    ("ck_pokemon_hp", "hp IS NULL OR (hp BETWEEN 10 AND 1000)"),
    ("ck_pokemon_iv_atk", "iv_atk IS NULL OR (iv_atk BETWEEN 0 AND 15)"),
    ("ck_pokemon_iv_def", "iv_def IS NULL OR (iv_def BETWEEN 0 AND 15)"),
    ("ck_pokemon_iv_sta", "iv_sta IS NULL OR (iv_sta BETWEEN 0 AND 15)"),
    ("ck_pokemon_iv_status", "iv_status IN ('unknown', 'range', 'verified')"),
    ("ck_pokemon_verified_ivs_present",
     "iv_status != 'verified' OR (iv_atk IS NOT NULL AND iv_def IS NOT NULL AND iv_sta IS NOT NULL)"),
    ("ck_pokemon_level", "level IS NULL OR (level BETWEEN 1 AND 51)"),
    ("ck_pokemon_weight", "weight_kg IS NULL OR weight_kg > 0"),
    ("ck_pokemon_height", "height_m IS NULL OR height_m > 0"),
]


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("lease_token", sa.String(length=36), nullable=True))
    with op.batch_alter_table("scans") as b:
        b.add_column(sa.Column("current_attempt_id", sa.String(length=36), nullable=True))
    for table in ("frames", "observations", "appraisals"):
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column("attempt_id", sa.String(length=36), nullable=True))
            b.create_index(f"ix_{table}_attempt_id", ["attempt_id"])
    with op.batch_alter_table("observation_groups") as b:
        b.add_column(sa.Column("attempt_id", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.create_index("ix_observation_groups_attempt_id", ["attempt_id"])
        b.drop_constraint("uq_group_track", type_="unique")
        b.create_unique_constraint("uq_group_attempt_track", ["scan_id", "attempt_id", "track_key"])

    # Clean up anything earlier versions could have let through before the constraints arrive.
    op.execute("UPDATE pokemon SET iv_status = 'unknown' WHERE iv_status NOT IN ('unknown', 'range', 'verified')")
    op.execute("UPDATE pokemon SET iv_status = 'range' WHERE iv_status = 'verified' AND "
               "(iv_atk IS NULL OR iv_def IS NULL OR iv_sta IS NULL)")
    with op.batch_alter_table("pokemon") as b:
        for name, cond in POKEMON_CHECKS:
            b.create_check_constraint(name, cond)

    op.create_index("uq_scans_sha256_real", "scans", ["sha256"], unique=True,
                    sqlite_where=sa.text("is_demo = 0"), postgresql_where=sa.text("is_demo = false"))
    op.create_index("uq_jobs_one_active_per_scan", "jobs", ["scan_id"], unique=True,
                    sqlite_where=sa.text("status IN ('queued', 'running')"),
                    postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index("uq_jobs_one_active_per_scan", table_name="jobs")
    op.drop_index("uq_scans_sha256_real", table_name="scans")
    with op.batch_alter_table("pokemon") as b:
        for name, _ in POKEMON_CHECKS:
            b.drop_constraint(name, type_="check")
    with op.batch_alter_table("observation_groups") as b:
        b.drop_constraint("uq_group_attempt_track", type_="unique")
        b.create_unique_constraint("uq_group_track", ["scan_id", "track_key"])
        b.drop_index("ix_observation_groups_attempt_id")
        b.drop_column("published")
        b.drop_column("attempt_id")
    for table in ("frames", "observations", "appraisals"):
        with op.batch_alter_table(table) as b:
            b.drop_index(f"ix_{table}_attempt_id")
            b.drop_column("attempt_id")
    with op.batch_alter_table("scans") as b:
        b.drop_column("current_attempt_id")
    with op.batch_alter_table("jobs") as b:
        b.drop_column("lease_token")
