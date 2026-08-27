from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from dotenv import load_dotenv
import os

from extensions import db

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_USERNAME")

db.init_app(app)
migrate = Migrate(app, db)
mail = Mail(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

import models  # noqa: E402


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(models.User, int(user_id))


def get_stage_names():
    return [s.name for s in models.Stage.query.order_by(models.Stage.position).all()]


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if current_user.role not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


@app.before_request
def require_password_change():
    if current_user.is_authenticated and getattr(current_user, "must_change_password", False):
        allowed = {"change_password", "logout", "static"}
        if request.endpoint not in allowed:
            return redirect(url_for("change_password"))


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


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
            if user.must_change_password:
                return redirect(url_for("change_password"))
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")
        user = models.User.query.filter_by(email=email).first()
        if user:
            token_str = models.PasswordResetToken.generate_token()
            reset_token = models.PasswordResetToken(
                user_id=user.id,
                token=token_str,
                expires_at=datetime.now() + timedelta(minutes=30),
            )
            db.session.add(reset_token)
            db.session.commit()

            reset_url = url_for("reset_password", token=token_str, _external=True)
            msg = Message("Reset your CRM password", recipients=[user.email])
            msg.body = (
                f"Hi {user.name},\n\n"
                f"Click this link to reset your password (valid for 30 minutes):\n\n"
                f"{reset_url}\n\n"
                f"If you didn't request this, you can ignore this email."
            )
            mail.send(msg)

        flash("If that email exists in our system, a reset link has been sent.")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset_token = models.PasswordResetToken.query.filter_by(token=token).first()

    if not reset_token or not reset_token.is_valid():
        flash("This reset link is invalid or has expired. Please request a new one.")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if new_password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("reset_password", token=token))

        user = reset_token.user

        if user.check_password(new_password):
            flash("Your new password must be different from your current password.")
            return redirect(url_for("reset_password", token=token))

        user.set_password(new_password)
        user.must_change_password = False
        reset_token.used = True
        db.session.commit()

        flash("Your password has been reset. Please sign in.")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    forced = current_user.must_change_password
    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not forced:
            current_password = request.form.get("current_password")
            if not current_user.check_password(current_password):
                flash("Your current password is incorrect.")
                return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("New password and confirmation do not match.")
            return redirect(url_for("change_password"))

        if current_user.check_password(new_password):
            flash("Your new password must be different from your current password.")
            return redirect(url_for("change_password"))

        current_user.set_password(new_password)
        current_user.must_change_password = False
        db.session.commit()
        flash("Password updated successfully.")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html", forced=forced)


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


@app.route("/companies/<int:company_id>")
@login_required
def company_detail(company_id):
    company = models.Company.query.get_or_404(company_id)
    activities = (
        models.Activity.query
        .filter_by(related_type="Company", related_id=company_id)
        .order_by(models.Activity.created_at.desc())
        .all()
    )
    return render_template("company_detail.html", company=company, activities=activities)


@app.route("/companies/<int:company_id>/activities/add", methods=["POST"])
@login_required
def add_company_activity(company_id):
    models.Company.query.get_or_404(company_id)
    activity = models.Activity(
        related_type="Company",
        related_id=company_id,
        type=request.form.get("type"),
        notes=request.form.get("notes"),
    )
    db.session.add(activity)
    db.session.commit()
    return redirect(url_for("company_detail", company_id=company_id))


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "sales_manager")
def delete_company(company_id):
    company = models.Company.query.get_or_404(company_id)
    contact_count = models.Contact.query.filter_by(company_id=company.id).count()
    lead_count = models.Lead.query.filter_by(company_id=company.id).count()
    deal_count = models.Deal.query.filter_by(company_id=company.id).count()
    if contact_count > 0 or lead_count > 0 or deal_count > 0:
        flash(f'Cannot delete "{company.name}" — it still has {contact_count} contact(s), {lead_count} lead(s), and {deal_count} deal(s) linked to it. Remove or reassign them first.')
        return redirect(url_for("companies"))
    models.Activity.query.filter_by(related_type="Company", related_id=company.id).delete()
    db.session.delete(company)
    db.session.commit()
    flash(f'"{company.name}" was deleted.')
    return redirect(url_for("companies"))


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
            role_title=request.form.get("role_title"),
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
        contact.role_title = request.form.get("role_title")
        contact.email = request.form.get("email")
        contact.phone = request.form.get("phone")
        db.session.commit()
        return redirect(url_for("contacts"))
    return render_template("contact_form.html", contact=contact, companies=all_companies)


@app.route("/contacts/<int:contact_id>")
@login_required
def contact_detail(contact_id):
    contact = models.Contact.query.get_or_404(contact_id)
    activities = (
        models.Activity.query
        .filter_by(related_type="Contact", related_id=contact_id)
        .order_by(models.Activity.created_at.desc())
        .all()
    )
    return render_template("contact_detail.html", contact=contact, activities=activities)


@app.route("/contacts/<int:contact_id>/activities/add", methods=["POST"])
@login_required
def add_contact_activity(contact_id):
    models.Contact.query.get_or_404(contact_id)
    activity = models.Activity(
        related_type="Contact",
        related_id=contact_id,
        type=request.form.get("type"),
        notes=request.form.get("notes"),
    )
    db.session.add(activity)
    db.session.commit()
    return redirect(url_for("contact_detail", contact_id=contact_id))


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "sales_manager")
def delete_contact(contact_id):
    contact = models.Contact.query.get_or_404(contact_id)
    deal_count = models.Deal.query.filter_by(contact_id=contact.id).count()
    if deal_count > 0:
        flash(f'Cannot delete "{contact.name}" — {deal_count} deal(s) still list them as the primary contact. Update those deals first.')
        return redirect(url_for("contacts"))
    models.Activity.query.filter_by(related_type="Contact", related_id=contact.id).delete()
    db.session.delete(contact)
    db.session.commit()
    flash(f'"{contact.name}" was deleted.')
    return redirect(url_for("contacts"))


@app.route("/leads")
@login_required
def leads():
    query = models.Lead.query
    if current_user.role == "account_executive":
        query = query.filter_by(assigned_rep_id=current_user.id)
    all_leads = query.order_by(models.Lead.created_at.desc()).all()
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
    if current_user.role == "account_executive" and lead.assigned_rep_id != current_user.id:
        abort(403)
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


@app.route("/leads/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    lead = models.Lead.query.get_or_404(lead_id)
    if current_user.role == "account_executive" and lead.assigned_rep_id != current_user.id:
        abort(403)
    activities = (
        models.Activity.query
        .filter_by(related_type="Lead", related_id=lead_id)
        .order_by(models.Activity.created_at.desc())
        .all()
    )
    return render_template("lead_detail.html", lead=lead, activities=activities)


@app.route("/leads/<int:lead_id>/activities/add", methods=["POST"])
@login_required
def add_lead_activity(lead_id):
    lead = models.Lead.query.get_or_404(lead_id)
    if current_user.role == "account_executive" and lead.assigned_rep_id != current_user.id:
        abort(403)
    activity = models.Activity(
        related_type="Lead",
        related_id=lead_id,
        type=request.form.get("type"),
        notes=request.form.get("notes"),
    )
    db.session.add(activity)
    db.session.commit()
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
def delete_lead(lead_id):
    lead = models.Lead.query.get_or_404(lead_id)
    if current_user.role == "account_executive" and lead.assigned_rep_id != current_user.id:
        abort(403)
    models.Activity.query.filter_by(related_type="Lead", related_id=lead.id).delete()
    db.session.delete(lead)
    db.session.commit()
    flash("Lead was deleted.")
    return redirect(url_for("leads"))


@app.route("/deals")
@login_required
def deals():
    query = models.Deal.query
    if current_user.role == "account_executive":
        query = query.filter_by(owner_id=current_user.id)
    all_deals = query.order_by(models.Deal.id.desc()).all()
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
            requirements=request.form.get("requirements"),
            budget=request.form.get("budget") or None,
        )
        db.session.add(deal)
        db.session.commit()
        return redirect(url_for("deals"))
    return render_template(
        "deal_form.html", companies=all_companies, contacts=all_contacts,
        users=all_users, stages=get_stage_names()
    )


@app.route("/deals/<int:deal_id>/edit", methods=["GET", "POST"])
@login_required
def edit_deal(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    if current_user.role == "account_executive" and deal.owner_id != current_user.id:
        abort(403)
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
        deal.requirements = request.form.get("requirements")
        deal.budget = request.form.get("budget") or None
        db.session.commit()
        return redirect(url_for("deals"))
    return render_template(
        "deal_form.html", deal=deal, companies=all_companies, contacts=all_contacts,
        users=all_users, stages=get_stage_names()
    )


@app.route("/deals/<int:deal_id>")
@login_required
def deal_detail(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    if current_user.role == "account_executive" and deal.owner_id != current_user.id:
        abort(403)
    activities = (
        models.Activity.query
        .filter_by(related_type="Deal", related_id=deal_id)
        .order_by(models.Activity.created_at.desc())
        .all()
    )
    return render_template("deal_detail.html", deal=deal, activities=activities)


@app.route("/deals/<int:deal_id>/activities/add", methods=["POST"])
@login_required
def add_deal_activity(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    if current_user.role == "account_executive" and deal.owner_id != current_user.id:
        abort(403)
    activity = models.Activity(
        related_type="Deal",
        related_id=deal_id,
        type=request.form.get("type"),
        notes=request.form.get("notes"),
    )
    db.session.add(activity)
    db.session.commit()
    return redirect(url_for("deal_detail", deal_id=deal_id))


@app.route("/deals/<int:deal_id>/delete", methods=["POST"])
@login_required
def delete_deal(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    if current_user.role == "account_executive" and deal.owner_id != current_user.id:
        abort(403)
    models.Activity.query.filter_by(related_type="Deal", related_id=deal.id).delete()
    db.session.delete(deal)
    db.session.commit()
    flash("Deal was deleted.")
    return redirect(url_for("deals"))


@app.route("/pipeline")
@login_required
def pipeline():
    stages = get_stage_names()
    query = models.Deal.query
    if current_user.role == "account_executive":
        query = query.filter_by(owner_id=current_user.id)
    all_deals = query.all()
    deals_by_stage = {s: [] for s in stages}
    for d in all_deals:
        if d.stage in deals_by_stage:
            deals_by_stage[d.stage].append(d)
    return render_template("pipeline.html", stages=stages, deals_by_stage=deals_by_stage)


@app.route("/deals/<int:deal_id>/update_stage", methods=["POST"])
@login_required
def update_deal_stage(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    if current_user.role == "account_executive" and deal.owner_id != current_user.id:
        abort(403)
    data = request.get_json()
    new_stage = data.get("stage") if data else None
    if new_stage in get_stage_names():
        deal.stage = new_stage
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False}), 400


@app.route("/stages")
@login_required
@role_required("admin", "sales_manager")
def manage_stages():
    all_stages = models.Stage.query.order_by(models.Stage.position).all()
    deal_counts = {}
    for stage in all_stages:
        deal_counts[stage.name] = models.Deal.query.filter_by(stage=stage.name).count()
    return render_template("stages_list.html", stages=all_stages, deal_counts=deal_counts)


@app.route("/stages/add", methods=["POST"])
@login_required
@role_required("admin", "sales_manager")
def add_stage():
    name = (request.form.get("name") or "").strip().lower()
    position_raw = request.form.get("position") or "0"
    try:
        position = int(position_raw)
    except ValueError:
        flash("Position must be a number.")
        return redirect(url_for("manage_stages"))
    if name:
        existing = models.Stage.query.filter_by(name=name).first()
        if not existing:
            db.session.add(models.Stage(name=name, position=position))
            db.session.commit()
    return redirect(url_for("manage_stages"))


@app.route("/stages/<int:stage_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "sales_manager")
def delete_stage(stage_id):
    stage = models.Stage.query.get_or_404(stage_id)
    deals_using_it = models.Deal.query.filter_by(stage=stage.name).count()
    if deals_using_it > 0:
        flash(f'Cannot delete "{stage.name}" — {deals_using_it} deal(s) are currently using it. Move them to another stage first.')
        return redirect(url_for("manage_stages"))
    db.session.delete(stage)
    db.session.commit()
    return redirect(url_for("manage_stages"))


@app.route("/users")
@login_required
@role_required("admin", "sales_manager")
def users():
    all_users = models.User.query.order_by(models.User.name).all()
    return render_template("users_list.html", users=all_users)


@app.route("/users/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "sales_manager")
def add_user():
    if request.method == "POST":
        existing = models.User.query.filter_by(email=request.form.get("email")).first()
        if existing:
            flash("A user with that email already exists.")
            return redirect(url_for("add_user"))
        new_user = models.User(
            name=request.form.get("name"),
            email=request.form.get("email"),
            role=request.form.get("role"),
            must_change_password=True,
        )
        new_user.set_password(request.form.get("password"))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for("users"))
    return render_template("user_form.html")


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "sales_manager")
def edit_user(user_id):
    user = models.User.query.get_or_404(user_id)
    if request.method == "POST":
        user.name = request.form.get("name")
        user.email = request.form.get("email")
        user.role = request.form.get("role")
        db.session.commit()
        return redirect(url_for("users"))
    return render_template("user_form.html", user=user)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "sales_manager")
def delete_user(user_id):
    user = models.User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.")
        return redirect(url_for("users"))

    if user.role == "admin":
        admin_count = models.User.query.filter_by(role="admin").count()
        if admin_count <= 1:
            flash("Cannot delete the last remaining Admin account.")
            return redirect(url_for("users"))

    deal_count = models.Deal.query.filter_by(owner_id=user.id).count()
    lead_count = models.Lead.query.filter_by(assigned_rep_id=user.id).count()
    if deal_count > 0 or lead_count > 0:
        flash(f'Cannot delete "{user.name}" — they still own {deal_count} deal(s) and are assigned {lead_count} lead(s). Reassign these first.')
        return redirect(url_for("users"))

    db.session.delete(user)
    db.session.commit()
    flash(f'"{user.name}" was deleted.')
    return redirect(url_for("users"))


if __name__ == "__main__":
    app.run(debug=True)