from app import app
from extensions import db
from models import User

with app.app_context():
    existing = User.query.filter_by(email="fazhan@example.com").first()
    if existing:
        print("User already exists.")
    else:
        user = User(name="Fazhan", email="fazhan@example.com", role="admin")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        print("User created! Email: fazhan@example.com  Password: password123")