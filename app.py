from flask import Flask
from flask_migrate import Migrate
from flask_login import LoginManager
from dotenv import load_dotenv
import os

from extensions import db

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

db.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

import models  # noqa: E402


@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))


@app.route("/")
def home():
    return "<h1>My CRM is working! 🎉</h1>"


if __name__ == "__main__":
    app.run(debug=True)