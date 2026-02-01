"""
Database service layer - centralized data operations.

This module provides a clean interface for database operations,
separating business logic from direct database access.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy import func
from database.models import (
    db, BookTitle, Inventory, Cabinet, AuditLog, AdminUser, ViewEvent
)


class InventoryService:
    """Service for inventory-related operations."""

    @staticmethod
    def get_or_create_title(title: str, author: Optional[str] = None) -> BookTitle:
        """Get or create a BookTitle record."""
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Title cannot be empty")

        existing = BookTitle.query.filter_by(title=clean_title).first()
        if existing:
            if author and not existing.author:
                existing.author = author
                db.session.flush()
            return existing

        new_title = BookTitle(title=clean_title, author=(author or "").strip() or None)
        db.session.add(new_title)
        db.session.flush()
        return new_title

    @staticmethod
    def get_inventory_by_title(title: str) -> List[Inventory]:
        """Get all inventory records for a given title."""
        title_obj = BookTitle.query.filter_by(title=title).first()
        if not title_obj:
            return []
        return Inventory.query.filter_by(title_id=title_obj.id).all()

    @staticmethod
    def get_inventory_by_cabinet(cabinet_id: int) -> List[Inventory]:
        """Get all inventory records for a given cabinet."""
        return Inventory.query.filter_by(cabinet_id=cabinet_id).all()

    @staticmethod
    def create_or_update_inventory(
        title_id: int,
        cabinet_id: int
    ) -> Inventory:
        """Create or update an inventory record."""
        # Defensive validation
        if not title_id or title_id <= 0:
            raise ValueError(f"title_id must be a positive integer, got: {title_id}")
        if not cabinet_id or cabinet_id <= 0:
            raise ValueError(f"cabinet_id must be a positive integer, got: {cabinet_id}")
            
        # Verify title exists
        title_obj = BookTitle.query.get(title_id)
        if not title_obj:
            raise ValueError(f"BookTitle with id={title_id} does not exist")
            
        # Verify cabinet exists
        cabinet = Cabinet.query.get(cabinet_id)
        if not cabinet:
            raise ValueError(f"Cabinet with id={cabinet_id} does not exist")
        
        inventory = Inventory.query.filter_by(
            title_id=title_id,
            cabinet_id=cabinet_id
        ).first()

        if inventory:
            inventory.updated_at = datetime.utcnow()
        else:
            inventory = Inventory(
                title_id=title_id,
                cabinet_id=cabinet_id
            )
            db.session.add(inventory)

        db.session.flush()
        return inventory

    @staticmethod
    def adjust_quantity(inventory_id: int, delta: int) -> Optional[Inventory]:
        """Adjust inventory quantity by delta. Returns None if deleted.
        
        Note: Since quantity tracking is removed, this method now just
        deletes the inventory if delta would result in <= 0, otherwise
        updates the timestamp.
        """
        inventory = Inventory.query.get(inventory_id)
        if not inventory:
            return None

        # Since we don't track quantity, if delta would make it <= 0, delete it
        if delta < 0:
            db.session.delete(inventory)
            db.session.flush()
            return None

        inventory.updated_at = datetime.utcnow()
        db.session.flush()
        return inventory

    @staticmethod
    def delete_inventory(inventory_id: int) -> bool:
        """Delete an inventory record."""
        inventory = Inventory.query.get(inventory_id)
        if not inventory:
            return False
        db.session.delete(inventory)
        db.session.flush()
        return True


class CabinetService:
    """Service for cabinet-related operations."""

    @staticmethod
    def get_or_create_cabinet(name: str, cab_type: str = "display") -> Cabinet:
        """Get or create a cabinet."""
        cabinet = Cabinet.query.filter_by(name=name).first()
        if not cabinet:
            cabinet = Cabinet(name=name, type=cab_type)
            db.session.add(cabinet)
            db.session.flush()
        return cabinet

    @staticmethod
    def get_all_cabinets() -> List[Cabinet]:
        """Get all cabinets."""
        return Cabinet.query.order_by(Cabinet.name).all()


def get_top_books(limit: int = 10) -> List[BookTitle]:
    """Return trending books by view_count, excluding archived inventory."""
    return (
        BookTitle.query.join(Inventory)
        .filter(Inventory.status == "active")
        .order_by(BookTitle.view_count.desc(), BookTitle.updated_at.desc())
        .distinct()
        .limit(limit)
        .all()
    )

    @staticmethod
    def update_cabinet(cabinet_id: int, name: Optional[str] = None, cab_type: Optional[str] = None) -> Optional[Cabinet]:
        """Update cabinet name and/or type."""
        cabinet = Cabinet.query.get(cabinet_id)
        if not cabinet:
            return None

        if name is not None:
            cabinet.name = name.strip()
        if cab_type is not None:
            cabinet.type = cab_type

        db.session.flush()
        return cabinet

    @staticmethod
    def delete_cabinet(cabinet_id: int) -> bool:
        """Delete a cabinet if it has no inventory."""
        cabinet = Cabinet.query.get(cabinet_id)
        if not cabinet:
            return False

        if cabinet.books:
            return False  # Cannot delete cabinet with books

        db.session.delete(cabinet)
        db.session.flush()
        return True


class AuditService:
    """Service for audit logging."""

    @staticmethod
    def log_action(
        action: str,
        actor: str,
        target: Optional[str] = None,
        details: Optional[str] = None
    ) -> AuditLog:
        """Create an audit log entry."""
        entry = AuditLog(
            actor=actor,
            action=action,
            target=target,
            details=details
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    @staticmethod
    def get_recent_logs(limit: int = 20) -> List[AuditLog]:
        """Get recent audit logs."""
        return AuditLog.query.order_by(AuditLog.created_at.desc()).limit(limit).all()


class ViewEventService:
    """Service for view event tracking."""

    @staticmethod
    def log_view(title: str, source: Optional[str] = None, actor: Optional[str] = None) -> ViewEvent:
        """Log a view event."""
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Title cannot be empty")

        event = ViewEvent(
            title=clean_title,
            source=(source or "").strip() or None,
            actor=(actor or "").strip() or None
        )
        db.session.add(event)
        db.session.flush()
        return event

    @staticmethod
    def get_top_viewed_titles(limit: int = 10) -> List[Dict[str, Any]]:
        """Get most viewed titles."""
        results = (
            db.session.query(
                ViewEvent.title,
                func.count(ViewEvent.id).label("count")
            )
            .filter(ViewEvent.title != None)  # noqa: E711
            .group_by(ViewEvent.title)
            .order_by(func.count(ViewEvent.id).desc())
            .limit(limit)
            .all()
        )

        titles = [r.title for r in results]
        title_objects = {
            bt.title: bt
            for bt in BookTitle.query.filter(BookTitle.title.in_(titles)).all()
        }

        return [
            {
                "title": title,
                "count": count,
                "book_title": title_objects.get(title)
            }
            for title, count in results
        ]


# Convenience functions for backward compatibility
def get_or_create_title(title: str, author: Optional[str] = None) -> BookTitle:
    """Backward compatibility wrapper."""
    return InventoryService.get_or_create_title(title, author)

