from datetime import datetime
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


@app.route("/companies")
@login_required
def companies():
    all_companies = models.Company.query.order_by(models.Company.name).all()
    return render_template("companies_list.html", companies=all_companies)


@app.route("/companies/add", methods=["GET", "POST"])
@login_required
def add_company():
    if request.method == "POST":
        company = models.Company(
            name=request.form.get("name"),
            industry=request.form.get("industry"),
            phone=request.form.get("phone"),
            address=request.form.get("address"),
        )
        db.session.add(company)
        db.session.commit()
        return redirect(url_for("companies"))
    return render_template("company_form.html")


@app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
@login_required
def edit_company(company_id):
    company = models.Company.query.get_or_404(company_id)
    if request.method == "POST":
        company.name = request.form.get("name")
        company.industry = request.form.get("industry")
        company.phone = request.form.get("phone")
        company.address = request.form.get("address")
        db.session.commit()
        return redirect(url_for("companies"))
    return render_template("company_form.html", company=company)


@app.route("/contacts")
@login_required
def contacts():
    all_contacts = models.Contact.query.order_by(models.Contact.name).all()
    return render_template("contacts_list.html", contacts=all_contacts)


@app.route("/contacts/add", methods=["GET", "POST"])
@login_required
def add_contact():
    all_companies = models.Company.query.order_by(models.Company.name).all()
    if request.method == "POST":
        contact = models.Contact(
            company_id=request.form.get("company_id"),
            name=request.form.get("name"),
            email=request.form.get("email"),
            phone=request.form.get("phone"),
        )
        db.session.add(contact)
        db.session.commit()
        return redirect(url_for("contacts"))
    return render_template("contact_form.html", companies=all_companies)


@app.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contact(contact_id):
    contact = models.Contact.query.get_or_404(contact_id)
    all_companies = models.Company.query.order_by(models.Company.name).all()
    if request.method == "POST":
        contact.company_id = request.form.get("company_id")
        contact.name = request.form.get("name")
        contact.email = request.form.get("email")
        contact.phone = request.form.get("phone")
        db.session.commit()
        return redirect(url_for("contacts"))
    return render_template("contact_form.html", contact=contact, companies=all_companies)


@app.route("/leads")
@login_required
def leads():
    all_leads = models.Lead.query.order_by(models.Lead.created_at.desc()).all()
    return render_template("leads_list.html", leads=all_leads)


@app.route("/leads/add", methods=["GET", "POST"])
@login_required
def add_lead():
    all_companies = models.Company.query.order_by(models.Company.name).all()
    all_users = models.User.query.order_by(models.User.name).all()
    if request.method == "POST":
        lead = models.Lead(
            company_id=request.form.get("company_id") or None,
            assigned_rep_id=request.form.get("assigned_rep_id") or None,
            source=request.form.get("source"),
            status=request.form.get("status"),
        )
        db.session.add(lead)
        db.session.commit()
        return redirect(url_for("leads"))
    return render_template("lead_form.html", companies=all_companies, users=all_users)


@app.route("/leads/<int:lead_id>/edit", methods=["GET", "POST"])
@login_required
def edit_lead(lead_id):
    lead = models.Lead.query.get_or_404(lead_id)
    all_companies = models.Company.query.order_by(models.Company.name).all()
    all_users = models.User.query.order_by(models.User.name).all()
    if request.method == "POST":
        lead.company_id = request.form.get("company_id") or None
        lead.assigned_rep_id = request.form.get("assigned_rep_id") or None
        lead.source = request.form.get("source")
        lead.status = request.form.get("status")
        db.session.commit()
        return redirect(url_for("leads"))
    return render_template("lead_form.html", lead=lead, companies=all_companies, users=all_users)


@app.route("/deals")
@login_required
def deals():
    all_deals = models.Deal.query.order_by(models.Deal.id.desc()).all()
    return render_template("deals_list.html", deals=all_deals)


@app.route("/deals/add", methods=["GET", "POST"])
@login_required
def add_deal():
    all_companies = models.Company.query.order_by(models.Company.name).all()
    all_contacts = models.Contact.query.order_by(models.Contact.name).all()
    all_users = models.User.query.order_by(models.User.name).all()
    if request.method == "POST":
        close_date_str = request.form.get("close_date")
        deal = models.Deal(
            company_id=request.form.get("company_id"),
            contact_id=request.form.get("contact_id") or None,
            owner_id=request.form.get("owner_id") or None,
            stage=request.form.get("stage"),
            value=request.form.get("value") or None,
            close_date=datetime.strptime(close_date_str, "%Y-%m-%d").date() if close_date_str else None,
        )
        db.session.add(deal)
        db.session.commit()
        return redirect(url_for("deals"))
    return render_template("deal_form.html", companies=all_companies, contacts=all_contacts, users=all_users)


@app.route("/deals/<int:deal_id>/edit", methods=["GET", "POST"])
@login_required
def edit_deal(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    all_companies = models.Company.query.order_by(models.Company.name).all()
    all_contacts = models.Contact.query.order_by(models.Contact.name).all()
    all_users = models.User.query.order_by(models.User.name).all()
    if request.method == "POST":
        close_date_str = request.form.get("close_date")
        deal.company_id = request.form.get("company_id")
        deal.contact_id = request.form.get("contact_id") or None
        deal.owner_id = request.form.get("owner_id") or None
        deal.stage = request.form.get("stage")
        deal.value = request.form.get("value") or None
        deal.close_date = datetime.strptime(close_date_str, "%Y-%m-%d").date() if close_date_str else None
        db.session.commit()
        return redirect(url_for("deals"))
    return render_template("deal_form.html", deal=deal, companies=all_companies, contacts=all_contacts, users=all_users)


if __name__ == "__main__":
    app.run(debug=True)