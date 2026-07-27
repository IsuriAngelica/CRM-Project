from flask import Flask, render_template, redirect, url_for, request, flash
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
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
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = models.User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)