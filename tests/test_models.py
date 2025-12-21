"""
Unit tests for database models.
"""
import unittest
from datetime import datetime
from app import app, db
from database.models import Book, Cabinet, BookTitle, Inventory, AuditLog, AdminUser


class TestModels(unittest.TestCase):
    """Test database models."""
    
    def setUp(self):
        """Set up test environment."""
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
    
    def tearDown(self):
        """Clean up after tests."""
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
    
    def test_book_title_creation(self):
        """Test creating a book title."""
        title = BookTitle(title="Test Book")
        db.session.add(title)
        db.session.commit()
        
        self.assertIsNotNone(title.id)
        self.assertEqual(title.title, "Test Book")
    
    def test_cabinet_creation(self):
        """Test creating a cabinet."""
        cabinet = Cabinet(name="Test Cabinet", type="display")
        db.session.add(cabinet)
        db.session.commit()
        
        self.assertIsNotNone(cabinet.id)
        self.assertEqual(cabinet.name, "Test Cabinet")
        self.assertEqual(cabinet.type, "display")
    
    def test_book_creation(self):
        """Test creating a book (inventory record)."""
        title = BookTitle(title="Test Book")
        cabinet = Cabinet(name="Test Cabinet")
        db.session.add(title)
        db.session.add(cabinet)
        db.session.commit()
        
        book = Book(title_id=title.id, cabinet_id=cabinet.id)
        db.session.add(book)
        db.session.commit()
        
        self.assertIsNotNone(book.id)
        self.assertEqual(book.title_id, title.id)
        self.assertEqual(book.cabinet_id, cabinet.id)


if __name__ == '__main__':
    unittest.main()

