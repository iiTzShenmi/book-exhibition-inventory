from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Cabinet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    type = db.Column(db.String(10), nullable=False, default="display")
    books = db.relationship("Inventory", backref="cabinet", lazy=True)


class BookTitle(db.Model):
    __tablename__ = "book_title"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), unique=True, nullable=False)
    author = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Inventory(db.Model):
    __tablename__ = "inventory"
    __table_args__ = (db.UniqueConstraint("title_id", "cabinet_id", name="uq_inventory_title_cabinet"),)

    id = db.Column(db.Integer, primary_key=True)
    title_id = db.Column(db.Integer, db.ForeignKey("book_title.id"), nullable=False)
    cabinet_id = db.Column(db.Integer, db.ForeignKey("cabinet.id"), nullable=False)
    qty_on_hand = db.Column(db.Integer, nullable=False, default=0)
    qty_reserved = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    book_title = db.relationship("BookTitle", backref="inventories", lazy=True)

    @property
    def title(self):
        return self.book_title.title if self.book_title else ""

    @property
    def author(self):
        return self.book_title.author if self.book_title else None

    @property
    def in_stock(self):
        return (self.qty_on_hand or 0) > 0


# Backward compatibility alias for existing imports
Book = Inventory
