from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()

class Cabinet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    type = db.Column(db.String(10), nullable=False, default="display")
    books = db.relationship('Book', backref='cabinet', lazy=True)

class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    author = db.Column(db.String(255))
    in_stock = db.Column(db.Boolean, default=True)
    cabinet_id = db.Column(db.Integer, db.ForeignKey('cabinet.id'))
    
