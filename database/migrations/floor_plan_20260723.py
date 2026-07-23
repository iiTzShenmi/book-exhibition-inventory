"""Create the persisted floor-plan layout table for display cabinets."""

from database.models import FloorPlanPosition


MIGRATION_ID = "floor_plan_20260723"


def upgrade(engine) -> None:
    """Create the idempotent floor-plan schema on SQLite and PostgreSQL."""
    FloorPlanPosition.__table__.create(engine, checkfirst=True)
