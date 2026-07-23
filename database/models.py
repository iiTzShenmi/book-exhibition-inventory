from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import validates
from sqlalchemy import event

db = SQLAlchemy()


class Cabinet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    type = db.Column(db.String(10), nullable=False, default="display")
    books = db.relationship("Inventory", backref="cabinet", lazy=True)


class FloorPlanPosition(db.Model):
    """Persisted display-cabinet geometry for the public exhibition map."""

    __tablename__ = "floor_plan_position"
    __table_args__ = (db.UniqueConstraint("cabinet_id", name="uq_floor_plan_position_cabinet"),)

    id = db.Column(db.Integer, primary_key=True)
    cabinet_id = db.Column(db.Integer, db.ForeignKey("cabinet.id"), nullable=False, index=True)
    left_percent = db.Column(db.Float, nullable=False)
    top_percent = db.Column(db.Float, nullable=False)
    width_percent = db.Column(db.Float, nullable=False)
    height_percent = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    cabinet = db.relationship("Cabinet", backref=db.backref("floor_plan_position", uselist=False))


class FloorPlanObject(db.Model):
    """Editable non-inventory features shown around exhibition cabinets."""

    __tablename__ = "floor_plan_object"

    id = db.Column(db.Integer, primary_key=True)
    object_key = db.Column(db.String(64), nullable=False, unique=True, index=True)
    kind = db.Column(db.String(24), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    left_percent = db.Column(db.Float, nullable=False)
    top_percent = db.Column(db.Float, nullable=False)
    width_percent = db.Column(db.Float, nullable=False)
    height_percent = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookTitle(db.Model):
    __tablename__ = "book_title"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), unique=True, nullable=False)
    author = db.Column(db.String(255))
    topics = db.Column(db.Text)  # JSON/text list of topics (prototype support)
    cover_link = db.Column(db.String(255))
    view_count = db.Column(db.Integer, nullable=False, default=0, index=True)
    last_viewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Association table for EventSchedule <-> BookTitle
event_books = db.Table(
    "event_books",
    db.Column("event_id", db.Integer, db.ForeignKey("event_schedule.id"), primary_key=True),
    db.Column("book_title_id", db.Integer, db.ForeignKey("book_title.id"), primary_key=True),
)


class Inventory(db.Model):
    __tablename__ = "inventory"
    __table_args__ = (db.UniqueConstraint("title_id", "cabinet_id", name="uq_inventory_title_cabinet"),)

    id = db.Column(db.Integer, primary_key=True)
    title_id = db.Column(db.Integer, db.ForeignKey("book_title.id"), nullable=False)
    cabinet_id = db.Column(db.Integer, db.ForeignKey("cabinet.id"), nullable=False)
    in_stock = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    book_title = db.relationship("BookTitle", backref="inventories", lazy=True)

    @validates("title_id")
    def validate_title_id(self, key, value):
        """Prevent NULL title_id from being set."""
        # Allow None during object deletion/cascade operations
        # SQLAlchemy may set to None temporarily during cascade deletes
        # We'll catch actual NULL values at the database level and in event listeners
        if value is None:
            # Check if this object is being deleted (has no id or is marked for deletion)
            # If so, allow None to pass through (will be caught by database constraint if it persists)
            from sqlalchemy import inspect
            try:
                state = inspect(self)
                if state.deleted or state.detached:
                    # Object is being deleted, allow None to pass (will be deleted anyway)
                    return value
            except Exception:
                # If we can't check state, be safe and reject None
                pass
            
            raise ValueError(
                "title_id cannot be NULL. This violates database constraints. "
                "Ensure a valid BookTitle exists before creating Inventory."
            )
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"title_id must be a positive integer, got: {value}")
        return value

    @validates("cabinet_id")
    def validate_cabinet_id(self, key, value):
        """Prevent NULL cabinet_id from being set."""
        if value is None:
            raise ValueError(
                "cabinet_id cannot be NULL. This violates database constraints. "
                "Ensure a valid Cabinet exists before creating Inventory."
            )
        if not isinstance(value, int) or value <= 0:
            raise ValueError(f"cabinet_id must be a positive integer, got: {value}")
        return value

    @property
    def title(self):
        return self.book_title.title if self.book_title else ""

    @property
    def author(self):
        return self.book_title.author if self.book_title else None



# Backward compatibility alias for existing imports
Book = Inventory


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(100), nullable=False, default="system")
    action = db.Column(db.String(100), nullable=False)
    target = db.Column(db.String(255), nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class AdminUser(db.Model):
    __tablename__ = "admin_user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="admin")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminInvite(db.Model):
    __tablename__ = "admin_invite"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=True)
    code_lookup = db.Column(db.String(64), nullable=True, index=True)
    memo = db.Column(db.String(255))
    role = db.Column(db.String(50), nullable=False, default="admin")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)


class IssueReport(db.Model):
    """Public issue reports, kept separate from the immutable audit trail."""

    __tablename__ = "issue_report"

    id = db.Column(db.Integer, primary_key=True)
    reporter_name = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    source_path = db.Column(db.String(255), nullable=True)
    fingerprint = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


class ViewEvent(db.Model):
    __tablename__ = "view_event"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    source = db.Column(db.String(50))
    actor = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class TopSellerSnapshot(db.Model):
    __tablename__ = "top_seller_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    limit = db.Column(db.Integer, nullable=False, default=8, index=True)
    payload = db.Column(db.Text, nullable=False)
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class BackupArchive(db.Model):
    __tablename__ = "backup_archive"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    filename = db.Column(db.String(100))
    csv_content = db.Column(db.Text)
    note = db.Column(db.String(255))

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "filename": self.filename,
            "size_kb": self.size_kb,
        }

    @property
    def size_kb(self):
        return round(len(self.csv_content or "") / 1024, 2)


class EventSchedule(db.Model):
    __tablename__ = "event_schedule"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    date_start = db.Column(db.Date, nullable=True)
    date_end = db.Column(db.Date, nullable=True)
    time_text = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    location = db.Column(db.String(120))
    note = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    books = db.relationship(
        "BookTitle",
        secondary=event_books,
        backref=db.backref("events", lazy="dynamic"),
    )


# Event listener to catch any Inventory objects with NULL title_id before flush
@event.listens_for(Inventory, "before_insert", propagate=True)
@event.listens_for(Inventory, "before_update", propagate=True)
def validate_inventory_before_flush(mapper, connection, target):
    """Additional safety check: prevent NULL title_id/cabinet_id before database flush."""
    from sqlalchemy import inspect
    
    # Skip validation if object is being deleted (cascade operations may set to None)
    try:
        state = inspect(target)
        if state.deleted:
            # Object is being deleted, skip validation (will be removed anyway)
            return
    except Exception:
        # If we can't check state, proceed with validation
        pass
    
    if target.title_id is None:
        raise ValueError(
            f"Inventory object (id={target.id if hasattr(target, 'id') and target.id else 'new'}) "
            f"has NULL title_id. This will cause a database constraint violation. "
            f"Check the code that created this Inventory object."
        )
    if target.cabinet_id is None:
        raise ValueError(
            f"Inventory object (id={target.id if hasattr(target, 'id') and target.id else 'new'}) "
            f"has NULL cabinet_id. This will cause a database constraint violation. "
            f"Check the code that created this Inventory object."
        )
