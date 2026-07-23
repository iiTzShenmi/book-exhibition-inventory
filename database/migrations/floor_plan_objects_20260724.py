"""Create persisted surrounding objects for the exhibition floor plan."""

from database.models import FloorPlanObject


MIGRATION_ID = "floor_plan_objects_20260724"


def upgrade(engine) -> None:
    """Create the idempotent surrounding-object table on supported databases."""
    FloorPlanObject.__table__.create(engine, checkfirst=True)
