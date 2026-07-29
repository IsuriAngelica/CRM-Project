from app import app
from extensions import db
from models import Stage

DEFAULT_STAGES = ["new", "contacted", "proposal", "negotiation", "won", "lost"]

with app.app_context():
    for i, name in enumerate(DEFAULT_STAGES):
        existing = Stage.query.filter_by(name=name).first()
        if not existing:
            db.session.add(Stage(name=name, position=i))
    db.session.commit()
    print("Stages seeded:", DEFAULT_STAGES)