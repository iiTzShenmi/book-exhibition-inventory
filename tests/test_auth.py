"""
Unit tests for authentication and authorization.
"""
import unittest
from werkzeug.security import generate_password_hash
from app import app, db
from database.models import AdminUser


class TestAuth(unittest.TestCase):
    """Test authentication functionality."""
    
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
    
    def test_admin_user_creation(self):
        """Test creating an admin user."""
        user = AdminUser(
            username="testadmin",
            email="test@example.com",
            password_hash=generate_password_hash("testpass"),
            role="admin"
        )
        db.session.add(user)
        db.session.commit()
        
        self.assertIsNotNone(user.id)
        self.assertEqual(user.username, "testadmin")
        self.assertEqual(user.role, "admin")
    
    def test_password_hashing(self):
        """Test password hashing."""
        password = "testpass"
        hash1 = generate_password_hash(password)
        hash2 = generate_password_hash(password)
        
        # Hashes should be different (due to salt)
        self.assertNotEqual(hash1, hash2)
        
        # But both should verify correctly
        from werkzeug.security import check_password_hash
        self.assertTrue(check_password_hash(hash1, password))
        self.assertTrue(check_password_hash(hash2, password))


if __name__ == '__main__':
    unittest.main()

