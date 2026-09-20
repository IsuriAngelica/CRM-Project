from datetime import datetime, timedelta, date
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from dotenv import load_dotenv
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from smtplib import SMTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

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


MAX_MONEY = 9999999999.99

STAGE_PALETTE = [
    "bg-secondary", "bg-info text-dark", "bg-primary",
    "bg-warning text-dark", "bg-success", "bg-danger", "bg-dark",
]

BUILD_STATUSES = ["not_started", "in_progress", "blocked", "delivered"]

# ---------------------------------------------------------------------------
# ROLE PERMISSION GROUPS
# ---------------------------------------------------------------------------
MANAGERS = ("admin", "sales_manager")
SALES_EDITORS = ("admin", "sales_manager", "account_executive")
LEAD_EDITORS = ("admin", "sales_manager", "account_executive", "marketing")
READ_ONLY = ("delivery", "ceo")
LEADERSHIP = ("admin", "sales_manager", "ceo")
DELIVERY_TEAM = ("admin", "sales_manager", "delivery")


# Product validation and ownership rules shared by every write path.
ALL_ROLES = ("admin", "sales_manager", "account_executive", "marketing", "delivery", "ceo")
SALES_ROLES = SALES_EDITORS
LEAD_STATUSES = ("new", "contacted", "qualified", "lost")
LEAD_SOURCES = ("Inbound", "Outbound", "Partner", "Referral")
ACTIVITY_TYPES = ("Call", "Email", "Meeting", "Note")
CLOSED_STAGES = ("won", "lost")


class FormError(ValueError):
    pass


def text_field(name, label, maximum, required=False):
    value = (request.form.get(name) or "").strip()
    if required and not value:
        raise FormError(f"{label} is required.")
    if len(value) > maximum:
        raise FormError(f"{label} must be {maximum} characters or fewer.")
    return value or None


def email_field(name="email", required=False):
    value = text_field(name, "Email", 120, required)
    if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise FormError("Enter a valid email address.")
    return value.lower() if value else None


def phone_field(name="phone"):
    value = text_field(name, "Phone", 30)
    if value and (not re.fullmatch(r"[+\d\s().\-]+", value) or sum(c.isdigit() for c in value) < 5):
        raise FormError("Enter a phone number with at least five digits.")
    return value


def choice_field(name, choices, label):
    value = request.form.get(name)
    if value not in choices:
        raise FormError(f"Choose a valid {label}.")
    return value


def record_field(name, model, label, required=False):
    value = request.form.get(name)
    if not value:
        if required:
            raise FormError(f"Choose a {label}.")
        return None
    try:
        record = db.session.get(model, int(value))
    except (ValueError, TypeError, OverflowError):
        record = None
    if record is None:
        raise FormError(f"The selected {label} no longer exists. Choose another.")
    return record


def sales_users():
    return models.User.query.filter(models.User.role.in_(SALES_ROLES)).order_by(models.User.name).all()


def sales_owner(field):
    user = record_field(field, models.User, "sales representative")
    if user and user.role not in SALES_ROLES:
        raise FormError("Assign records only to an Admin, Sales Manager or Account Executive.")
    if current_user.role == "account_executive":
        if user and user.id != current_user.id:
            raise FormError("You can assign this record only to yourself. Ask a manager to reassign it.")
        user = current_user
    return user


def check_lead_access(lead):
    if current_user.role == "account_executive" and lead.assigned_rep_id != current_user.id:
        abort(403)


def check_deal_access(deal):
    if current_user.role == "account_executive" and deal.owner_id != current_user.id:
        abort(403)


def visible_deals():
    query = models.Deal.query
    if current_user.role == "account_executive":
        query = query.filter_by(owner_id=current_user.id)
    return query


def visible_leads():
    query = models.Lead.query
    if current_user.role == "account_executive":
        query = query.filter_by(assigned_rep_id=current_user.id)
    return query


def visible_reminders():
    query = models.Reminder.query
    if current_user.role == "account_executive":
        query = query.filter(db.or_(
            models.Reminder.deal_id.in_(db.select(models.Deal.id).where(models.Deal.owner_id == current_user.id)),
            models.Reminder.lead_id.in_(db.select(models.Lead.id).where(models.Lead.assigned_rep_id == current_user.id)),
        ))
    return query


def check_reminder_access(reminder):
    if current_user.role == "account_executive":
        if reminder.deal:
            check_deal_access(reminder.deal)
        elif reminder.lead:
            check_lead_access(reminder.lead)
        else:
            abort(403)


def date_field(name, label):
    raw = request.form.get(name)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise FormError(f"Enter a valid {label}.")


def apply_stage(deal, stage, actual_date=None, explicit_date=False):
    if stage not in get_stage_names():
        raise FormError("Choose a configured pipeline stage.")
    old_stage = deal.stage
    if old_stage == "won" and stage != "won" and deal.build_status not in (None, "not_started"):
        raise FormError("Delivery has started. Set its build status to Not started before reopening or losing this deal.")
    if stage in CLOSED_STAGES:
        if actual_date and actual_date > date.today():
            raise FormError("An actual close date cannot be in the future.")
        if explicit_date:
            deal.actual_close_date = actual_date or (date.today() if old_stage != stage else deal.actual_close_date)
        elif old_stage != stage:
            deal.actual_close_date = date.today()
        # Old closed deals with unknown dates remain unknown until reviewed.
    else:
        if actual_date:
            raise FormError("Actual close date is only available for Won or Lost deals.")
        deal.actual_close_date = None
    if old_stage == "won" and stage != "won":
        deal.mismatch_flagged = False
        deal.mismatch_note = None
    deal.stage = stage


def validate_password(password):
    if not password or len(password) < 6:
        raise FormError("Password must contain at least six characters.")
    if len(password) > 128:
        raise FormError("Password must contain no more than 128 characters.")


def form_problem(error):
    db.session.rollback()
    flash(str(error) if isinstance(error, FormError) else "That change conflicts with another record. Check the details and try again.", "error")


@app.context_processor
def product_helpers():
    def fv(name, default=""):
        return request.form.get(name, "") if request.method == "POST" else (default if default is not None else "")
    return dict(fv=fv, lead_sources=LEAD_SOURCES, lead_statuses=LEAD_STATUSES,
                all_roles=ALL_ROLES, sales_roles=SALES_ROLES)


def parse_money(raw, field_label):
    if raw is None or str(raw).strip() == "": return None, None
    try:
        amount = Decimal(str(raw))
        if not amount.is_finite(): raise InvalidOperation()
        if amount < 0: return None, f"{field_label} cannot be negative."
        if amount > Decimal("9999999999.99"): return None, f"{field_label} cannot be greater than 9,999,999,999.99."
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None
    except (InvalidOperation, ValueError):
        return None, f"{field_label} must be a finite number."



def lead_temperature(lead):
    """CRM-19: classify a lead as hot, warm or cold from its activity history."""
    activities = (
        models.Activity.query
        .filter_by(related_type="Lead", related_id=lead.id)
        .order_by(models.Activity.created_at.desc())
        .all()
    )
    count = len(activities)
    if count == 0:
        return "cold"

    last = activities[0].created_at
    days_since = (datetime.now() - last).days

    if count >= 3 and days_since <= 7:
        return "hot"
    if days_since <= 30:
        return "warm"
    return "cold"



@app.context_processor
def inject_ui_labels():
    labels = {
        "convert_lead": "Convert lead", "my_dashboard": "Overview", "dashboard": "Overview",
        "company_dashboard": "Company overview", "monthly_report": "Monthly report",
        "manage_stages": "Pipeline stages", "handoff": "Delivery handoff",
        "search": "Search records", "change_password": "Security",
    }
    endpoint = request.endpoint or ""
    section = "Insights" if endpoint in ("company_dashboard", "monthly_report") else (
        "Administration" if endpoint in ("users", "add_user", "edit_user", "manage_stages", "change_password") else "Workspace"
    )
    return dict(ui_page=labels.get(endpoint, endpoint.replace("_", " ").capitalize()),
                ui_section=section, ui_now=datetime.now())

@app.context_processor
def inject_helpers():
    colors = {}
    try:
        for i, name in enumerate(get_stage_names()):
            colors[name] = STAGE_PALETTE[i % len(STAGE_PALETTE)]
    except Exception:
        pass

    def stage_color(name):
        return colors.get(name, "bg-secondary")

    role = getattr(current_user, "role", None)
    return dict(
        stage_color=stage_color,
        lead_temperature=lead_temperature,
        can_manage=role in MANAGERS,
        can_edit_sales=role in SALES_EDITORS,
        can_edit_leads=role in LEAD_EDITORS,
        is_read_only=role in READ_ONLY,
        can_view_leadership=role in LEADERSHIP,
        can_update_build=role in DELIVERY_TEAM,
    )


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
        return redirect(url_for("my_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password", "")
        user = models.User.query.filter(db.func.lower(models.User.email) == email).first()

        if user and user.is_locked():
            seconds_left = (user.locked_until - datetime.now()).total_seconds()
            minutes_left = int(seconds_left // 60) + 1
            flash(f"This account is locked after too many failed attempts. Try again in {minutes_left} minute(s).")
            return render_template("login.html")

        if user and user.check_password(password):
            user.reset_failed_logins()
            db.session.commit()
            login_user(user)
            if user.must_change_password:
                return redirect(url_for("change_password"))
            return redirect(url_for("my_dashboard"))

        if user:
            user.register_failed_login()
            db.session.commit()
            if user.is_locked():
                flash("Too many failed attempts. This account is locked for 15 minutes.")
                return render_template("login.html")

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
        try:
            email = email_field(required=True)
        except FormError as error:
            flash(str(error), "error")
            return render_template("forgot_password.html")
        user = models.User.query.filter(db.func.lower(models.User.email) == email).first()
        if user:
            token_str = models.PasswordResetToken.generate_token()
            token = models.PasswordResetToken(user_id=user.id, token=token_str,
                expires_at=datetime.now() + timedelta(minutes=30))
            db.session.add(token)
            try:
                db.session.flush()
                reset_url = url_for("reset_password", token=token_str, _external=True)
                msg = Message("Reset your CRM password", recipients=[user.email])
                msg.body = f"Hi {user.name},\n\nReset your password within 30 minutes:\n{reset_url}\n\nIf you did not request this, ignore this email."
                mail.send(msg)
                db.session.commit()
            except (SMTPException, OSError, SQLAlchemyError):
                db.session.rollback()
                app.logger.error("Password reset email could not be sent; check mail service configuration.")
        flash("If that account exists and email delivery is available, a reset link has been sent. If it does not arrive, contact an administrator.")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")



@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset_token = models.PasswordResetToken.query.filter_by(token=token).first()
    if not reset_token or not reset_token.is_valid():
        flash("This reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        try:
            password = request.form.get("new_password", "")
            validate_password(password)
            if password != request.form.get("confirm_password"):
                raise FormError("Passwords do not match.")
            user = reset_token.user
            if user.check_password(password): raise FormError("Choose a password different from your current one.")
            user.set_password(password)
            user.must_change_password = False
            user.reset_failed_logins()
            models.PasswordResetToken.query.filter_by(user_id=user.id, used=False).update({"used": True})
            db.session.commit()
            flash("Password reset. Please sign in.", "success")
            return redirect(url_for("login"))
        except FormError as error: form_problem(error)
    return render_template("reset_password.html", token=token)



@app.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for("my_dashboard"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    forced = current_user.must_change_password
    if request.method == "POST":
        try:
            if not forced and not current_user.check_password(request.form.get("current_password", "")):
                raise FormError("Your current password is incorrect.")
            password = request.form.get("new_password", "")
            validate_password(password)
            if password != request.form.get("confirm_password"):
                raise FormError("New password and confirmation do not match.")
            if current_user.check_password(password): raise FormError("Choose a password different from your current one.")
            current_user.set_password(password)
            current_user.must_change_password = False
            models.PasswordResetToken.query.filter_by(user_id=current_user.id, used=False).update({"used": True})
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("my_dashboard"))
        except FormError as error: form_problem(error)
    return render_template("change_password.html", forced=forced)



# ---------------------------------------------------------------------------
# CRM-20: MY DASHBOARD
# ---------------------------------------------------------------------------
@app.route("/my-dashboard")
@login_required
def my_dashboard():
    if current_user.role == "account_executive":
        my_deals = models.Deal.query.filter_by(owner_id=current_user.id).all()
        my_leads = models.Lead.query.filter_by(assigned_rep_id=current_user.id).all()
    else:
        my_deals = models.Deal.query.all()
        my_leads = models.Lead.query.all()

    open_deals = [d for d in my_deals if d.stage not in ("won", "lost")]
    total_value = sum(float(d.value) for d in open_deals if d.value is not None)

    deal_ids = [d.id for d in my_deals]
    lead_ids = [l.id for l in my_leads]

    recent_activity = (
        models.Activity.query
        .filter(
            db.or_(
                db.and_(models.Activity.related_type == "Deal",
                        models.Activity.related_id.in_(deal_ids or [0])),
                db.and_(models.Activity.related_type == "Lead",
                        models.Activity.related_id.in_(lead_ids or [0])),
            )
        )
        .order_by(models.Activity.created_at.desc())
        .limit(8)
        .all()
    )

    upcoming_query = models.Reminder.query.filter(
        models.Reminder.remind_at <= datetime.now() + timedelta(days=7),
        models.Reminder.completed_at.is_(None)
    )
    if current_user.role == "account_executive":
        upcoming_query = upcoming_query.filter(db.or_(
            models.Reminder.deal_id.in_(deal_ids or [0]),
            models.Reminder.lead_id.in_(lead_ids or [0]),
        ))
    upcoming = upcoming_query.order_by(models.Reminder.remind_at).limit(8).all()

    return render_template(
        "my_dashboard.html",
        open_deals=open_deals,
        my_leads=my_leads,
        total_value=total_value,
        recent_activity=recent_activity,
        upcoming=upcoming,
        overview_deals=my_deals,
        overview_stages=get_stage_names(),
    )


# ---------------------------------------------------------------------------
# CRM-21: COMPANY DASHBOARD  (leadership view)
# ---------------------------------------------------------------------------
@app.route("/company-dashboard")
@login_required
@role_required(*LEADERSHIP)
def company_dashboard():
    all_deals = models.Deal.query.all()
    all_leads = models.Lead.query.all()

    stages = get_stage_names()
    deals_by_stage = {s: [] for s in stages}
    for d in all_deals:
        if d.stage in deals_by_stage:
            deals_by_stage[d.stage].append(d)

    stage_values = {
        s: sum(float(d.value) for d in deals_by_stage[s] if d.value is not None)
        for s in stages
    }

    won = [d for d in all_deals if d.stage == "won"]
    lost = [d for d in all_deals if d.stage == "lost"]
    closed_total = len(won) + len(lost)
    conversion = round((len(won) / closed_total) * 100, 1) if closed_total else 0.0

    open_deals = [d for d in all_deals if d.stage not in ("won", "lost")]
    pipeline_value = sum(float(d.value) for d in open_deals if d.value is not None)
    won_value = sum(float(d.value) for d in won if d.value is not None)

    leads_by_source = {}
    for l in all_leads:
        key = l.source or "Unspecified"
        leads_by_source[key] = leads_by_source.get(key, 0) + 1

    return render_template(
        "company_dashboard.html",
        stages=stages,
        deals_by_stage=deals_by_stage,
        stage_values=stage_values,
        won=won,
        lost=lost,
        conversion=conversion,
        pipeline_value=pipeline_value,
        won_value=won_value,
        total_deals=len(all_deals),
        total_leads=len(all_leads),
        leads_by_source=leads_by_source,
    )


# ---------------------------------------------------------------------------
# CRM-22: MONTHLY REPORT
# ---------------------------------------------------------------------------
@app.route("/reports/monthly")
@login_required
@role_required(*LEADERSHIP)
def monthly_report():
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
        if not 1900 <= year <= 9998 or not 1 <= month <= 12:
            raise ValueError()
    except (ValueError, TypeError):
        flash("Choose a month from 1 to 12 and a year from 1900 to 9998.", "error")
        year, month = today.year, today.month
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    closed = models.Deal.query.filter(models.Deal.stage.in_(CLOSED_STAGES),
        models.Deal.actual_close_date >= start, models.Deal.actual_close_date < end).all()
    won, lost = [d for d in closed if d.stage == "won"], [d for d in closed if d.stage == "lost"]
    leads = models.Lead.query.filter(models.Lead.created_at >= datetime(year, month, 1),
        models.Lead.created_at < datetime(end.year, end.month, 1)).all()
    sources = {}
    for lead in leads:
        key = lead.source or "Unspecified"
        sources[key] = sources.get(key, 0) + 1
    return render_template("monthly_report.html", month_name=start.strftime("%B %Y"), year=year, month=month,
        won=won, lost=lost, won_value=sum(d.value or 0 for d in won), lost_value=sum(d.value or 0 for d in lost),
        budget_total=sum(d.budget or 0 for d in closed),
        with_requirements=[d for d in closed if d.requirements and d.requirements.strip()],
        without_requirements=[d for d in closed if not d.requirements or not d.requirements.strip()],
        leads_this_month=leads, leads_by_source=sources,
        undated_closed=models.Deal.query.filter(models.Deal.stage.in_(CLOSED_STAGES), models.Deal.actual_close_date.is_(None)).all())



# ---------------------------------------------------------------------------
# CRM-18: REMINDERS
# ---------------------------------------------------------------------------
@app.route("/reminders")
@login_required
def reminders():
    state = request.args.get("state", "active")
    if state not in ("active", "completed", "all"): state = "active"
    query = visible_reminders()
    if state == "active": query = query.filter(models.Reminder.completed_at.is_(None))
    elif state == "completed": query = query.filter(models.Reminder.completed_at.is_not(None))
    return render_template("reminders_list.html", reminders=query.order_by(models.Reminder.remind_at).all(), now=datetime.now(), state=state)



@app.route("/reminders/add", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def add_reminder():
    if request.method == "POST":
        try:
            message = text_field("message", "Reminder message", 255, True)
            deal = record_field("deal_id", models.Deal, "deal")
            lead = record_field("lead_id", models.Lead, "lead")
            if bool(deal) == bool(lead): raise FormError("Link the reminder to exactly one lead or deal.")
            if deal: check_deal_access(deal)
            if lead: check_lead_access(lead)
            try: when = datetime.strptime(request.form.get("remind_at", ""), "%Y-%m-%dT%H:%M")
            except ValueError: raise FormError("Choose a valid reminder date and time.")
            db.session.add(models.Reminder(message=message, remind_at=when,
                deal_id=deal.id if deal else None, lead_id=lead.id if lead else None))
            db.session.commit()
            flash("Reminder created.", "success")
            return redirect(url_for("reminders"))
        except (FormError, IntegrityError) as error: form_problem(error)
    return render_template("reminder_form.html", deals=visible_deals().order_by(models.Deal.id.desc()).all(),
        leads=visible_leads().order_by(models.Lead.id.desc()).all())



@app.route("/reminders/<int:reminder_id>/delete", methods=["POST"])
@login_required
@role_required(*SALES_EDITORS)
def delete_reminder(reminder_id):
    reminder = models.Reminder.query.get_or_404(reminder_id)
    check_reminder_access(reminder)
    db.session.delete(reminder)
    db.session.commit()
    flash("Reminder deleted.", "success")
    return redirect(url_for("reminders"))



# ---------------------------------------------------------------------------
# CRM-23: DELIVERY HANDOFF VIEW
# ---------------------------------------------------------------------------
@app.route("/handoff")
@login_required
def handoff():
    won_deals = visible_deals().filter_by(stage="won").order_by(models.Deal.actual_close_date.desc()).all()
    return render_template("handoff.html", deals=won_deals, build_statuses=BUILD_STATUSES)



# ---------------------------------------------------------------------------
# CRM-24: BUILD STATUS AND MISMATCH FLAG
# ---------------------------------------------------------------------------
@app.route("/deals/<int:deal_id>/build-status", methods=["POST"])
@login_required
@role_required(*DELIVERY_TEAM)
def update_build_status(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    if deal.stage != "won":
        abort(403)
    try:
        status = choice_field("build_status", BUILD_STATUSES, "build status")
        note = text_field("mismatch_note", "Mismatch note", 20000)
        flagged = bool(request.form.get("mismatch_flagged"))
        if flagged and not note:
            raise FormError("Explain the mismatch before flagging it to sales.")
        deal.build_status, deal.mismatch_flagged, deal.mismatch_note = status, flagged, note
        db.session.commit()
        flash("Delivery status saved.", "success")
        return redirect(url_for("handoff"))
    except FormError as error:
        form_problem(error)
        return render_template("handoff.html", deals=visible_deals().filter_by(stage="won").all(),
            build_statuses=BUILD_STATUSES, failed_deal_id=deal.id), 400



# ---------------------------------------------------------------------------
# CRM-25: SEARCH
# ---------------------------------------------------------------------------
@app.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()

    companies_found = []
    contacts_found = []
    leads_found = []
    deals_found = []

    if q:
        like = f"%{q}%"

        companies_found = models.Company.query.filter(models.Company.name.ilike(like)).limit(20).all()

        contacts_found = models.Contact.query.filter(
            db.or_(
                models.Contact.name.ilike(like),
                models.Contact.email.ilike(like),
            )
        ).limit(20).all()

        lead_query = models.Lead.query.outerjoin(models.Company, models.Lead.company_id == models.Company.id).filter(db.or_(
            models.Lead.prospect_name.ilike(like), models.Lead.email.ilike(like),
            models.Lead.phone.ilike(like), models.Lead.company_name.ilike(like), models.Company.name.ilike(like)))
        if current_user.role == "account_executive":
            lead_query = lead_query.filter(models.Lead.assigned_rep_id == current_user.id)
        leads_found = lead_query.limit(20).all()

        deal_query = models.Deal.query.join(
            models.Company, models.Deal.company_id == models.Company.id
        ).filter(models.Company.name.ilike(like))
        if current_user.role == "account_executive":
            deal_query = deal_query.filter(models.Deal.owner_id == current_user.id)
        deals_found = deal_query.limit(20).all()

    total = len(companies_found) + len(contacts_found) + len(leads_found) + len(deals_found)

    return render_template(
        "search_results.html",
        q=q,
        companies=companies_found,
        contacts=contacts_found,
        leads=leads_found,
        deals=deals_found,
        total=total,
    )


# ---------------------------------------------------------------------------
# COMPANIES
# ---------------------------------------------------------------------------
@app.route("/companies")
@login_required
def companies():
    all_companies = models.Company.query.order_by(models.Company.name).all()
    return render_template("companies_list.html", companies=all_companies)


@app.route("/companies/add", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def add_company():
    return company_editor()



@app.route("/companies/<int:company_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def edit_company(company_id):
    return company_editor(models.Company.query.get_or_404(company_id))



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
@role_required(*SALES_EDITORS)
def add_company_activity(company_id):
    record = models.Company.query.get_or_404(company_id)
    try:
        activity_type = choice_field("type", ACTIVITY_TYPES, "activity type")
        notes = text_field("notes", "Activity notes", 20000, True)
        db.session.add(models.Activity(related_type="Company", related_id=company_id, type=activity_type, notes=notes))
        db.session.commit()
        flash("Activity saved.", "success")
        return redirect(url_for("company_detail", company_id=company_id))
    except FormError as error:
        form_problem(error)
        return render_template("company_detail.html", company=record,
            activities=models.Activity.query.filter_by(related_type="Company", related_id=company_id).order_by(models.Activity.created_at.desc()).all()), 400


@app.route("/companies/<int:company_id>/delete", methods=["POST"])
@login_required
@role_required(*MANAGERS)
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
    flash(f'"{company.name}" was deleted.', "success")
    return redirect(url_for("companies"))


# ---------------------------------------------------------------------------
# CONTACTS
# ---------------------------------------------------------------------------
@app.route("/contacts")
@login_required
def contacts():
    all_contacts = models.Contact.query.order_by(models.Contact.name).all()
    return render_template("contacts_list.html", contacts=all_contacts)


@app.route("/contacts/add", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def add_contact():
    return contact_editor()



@app.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def edit_contact(contact_id):
    return contact_editor(models.Contact.query.get_or_404(contact_id))



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
@role_required(*SALES_EDITORS)
def add_contact_activity(contact_id):
    record = models.Contact.query.get_or_404(contact_id)
    try:
        activity_type = choice_field("type", ACTIVITY_TYPES, "activity type")
        notes = text_field("notes", "Activity notes", 20000, True)
        db.session.add(models.Activity(related_type="Contact", related_id=contact_id, type=activity_type, notes=notes))
        db.session.commit()
        flash("Activity saved.", "success")
        return redirect(url_for("contact_detail", contact_id=contact_id))
    except FormError as error:
        form_problem(error)
        return render_template("contact_detail.html", contact=record,
            activities=models.Activity.query.filter_by(related_type="Contact", related_id=contact_id).order_by(models.Activity.created_at.desc()).all()), 400


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def delete_contact(contact_id):
    contact = models.Contact.query.get_or_404(contact_id)
    if models.Deal.query.filter_by(contact_id=contact.id).count() or models.Lead.query.filter_by(contact_id=contact.id).count():
        flash("This contact is linked to a lead or deal. Remove or reassign those links first.", "error")
        return redirect(url_for("contacts"))
    models.Activity.query.filter_by(related_type="Contact", related_id=contact.id).delete()
    db.session.delete(contact)
    db.session.commit()
    flash("Contact deleted.", "success")
    return redirect(url_for("contacts"))



# ---------------------------------------------------------------------------
# LEADS
# ---------------------------------------------------------------------------
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
@role_required(*LEAD_EDITORS)
def add_lead():
    return lead_editor()



@app.route("/leads/<int:lead_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(*LEAD_EDITORS)
def edit_lead(lead_id):
    lead = models.Lead.query.get_or_404(lead_id)
    check_lead_access(lead)
    return lead_editor(lead)



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
@role_required(*LEAD_EDITORS)
def add_lead_activity(lead_id):
    record = models.Lead.query.get_or_404(lead_id)
    check_lead_access(record)
    try:
        activity_type = choice_field("type", ACTIVITY_TYPES, "activity type")
        notes = text_field("notes", "Activity notes", 20000, True)
        db.session.add(models.Activity(related_type="Lead", related_id=lead_id, type=activity_type, notes=notes))
        db.session.commit()
        flash("Activity saved.", "success")
        return redirect(url_for("lead_detail", lead_id=lead_id))
    except FormError as error:
        form_problem(error)
        return render_template("lead_detail.html", lead=record,
            activities=models.Activity.query.filter_by(related_type="Lead", related_id=lead_id).order_by(models.Activity.created_at.desc()).all()), 400


@app.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
@role_required(*SALES_EDITORS)
def delete_lead(lead_id):
    lead = models.Lead.query.get_or_404(lead_id)
    check_lead_access(lead)
    if lead.converted_deal:
        flash("This lead is linked to a deal. Keep it as the source record.", "error")
        return redirect(url_for("lead_detail", lead_id=lead.id))
    models.Activity.query.filter_by(related_type="Lead", related_id=lead.id).delete()
    models.Reminder.query.filter_by(lead_id=lead.id).delete()
    db.session.delete(lead)
    db.session.commit()
    flash("Lead deleted.", "success")
    return redirect(url_for("leads"))



# ---------------------------------------------------------------------------
# DEALS
# ---------------------------------------------------------------------------
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
@role_required(*SALES_EDITORS)
def add_deal():
    return deal_editor()



@app.route("/deals/<int:deal_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def edit_deal(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    check_deal_access(deal)
    return deal_editor(deal)



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
@role_required(*SALES_EDITORS)
def add_deal_activity(deal_id):
    record = models.Deal.query.get_or_404(deal_id)
    check_deal_access(record)
    try:
        activity_type = choice_field("type", ACTIVITY_TYPES, "activity type")
        notes = text_field("notes", "Activity notes", 20000, True)
        db.session.add(models.Activity(related_type="Deal", related_id=deal_id, type=activity_type, notes=notes))
        db.session.commit()
        flash("Activity saved.", "success")
        return redirect(url_for("deal_detail", deal_id=deal_id))
    except FormError as error:
        form_problem(error)
        return render_template("deal_detail.html", deal=record,
            activities=models.Activity.query.filter_by(related_type="Deal", related_id=deal_id).order_by(models.Activity.created_at.desc()).all()), 400


@app.route("/deals/<int:deal_id>/delete", methods=["POST"])
@login_required
@role_required(*SALES_EDITORS)
def delete_deal(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    check_deal_access(deal)
    if deal.source_lead:
        flash("This deal has a source lead. Mark it Lost instead of deleting its conversion history.", "error")
        return redirect(url_for("deal_detail", deal_id=deal.id))
    models.Activity.query.filter_by(related_type="Deal", related_id=deal.id).delete()
    models.Reminder.query.filter_by(deal_id=deal.id).delete()
    db.session.delete(deal)
    db.session.commit()
    flash("Deal deleted.", "success")
    return redirect(url_for("deals"))



# ---------------------------------------------------------------------------
# PIPELINE
# ---------------------------------------------------------------------------
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
@role_required(*SALES_EDITORS)
def update_deal_stage(deal_id):
    deal = models.Deal.query.get_or_404(deal_id)
    check_deal_access(deal)
    data = request.get_json(silent=True)
    try:
        if not isinstance(data, dict): raise FormError("Send a valid stage selection.")
        apply_stage(deal, data.get("stage"))
        db.session.commit()
        return jsonify(success=True, actual_close_date=deal.actual_close_date.isoformat() if deal.actual_close_date else None)
    except FormError as error:
        db.session.rollback()
        return jsonify(success=False, error=str(error)), 400



# ---------------------------------------------------------------------------
# STAGES
# ---------------------------------------------------------------------------
@app.route("/stages")
@login_required
@role_required(*MANAGERS)
def manage_stages():
    all_stages = models.Stage.query.order_by(models.Stage.position).all()
    deal_counts = {}
    for stage in all_stages:
        deal_counts[stage.name] = models.Deal.query.filter_by(stage=stage.name).count()
    return render_template("stages_list.html", stages=all_stages, deal_counts=deal_counts)


@app.route("/stages/add", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def add_stage():
    name = (request.form.get("name") or "").strip().lower()
    position_raw = request.form.get("position") or "0"
    try:
        position = int(position_raw)
    except ValueError:
        flash("Position must be a number.")
        return redirect(url_for("manage_stages"))
    if not name or len(name) > 50:
        flash("Stage name must contain 1 to 50 characters.")
        return redirect(url_for("manage_stages"))
    if models.Stage.query.filter_by(name=name).first():
        flash(f'A stage named "{name}" already exists.')
        return redirect(url_for("manage_stages"))
    db.session.add(models.Stage(name=name, position=position))
    db.session.commit()
    flash(f'Stage "{name}" was added.', "success")
    return redirect(url_for("manage_stages"))


@app.route("/stages/<int:stage_id>/delete", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def delete_stage(stage_id):
    stage = models.Stage.query.get_or_404(stage_id)
    if stage.name in CLOSED_STAGES:
        flash("Won and Lost are required by reporting and delivery and cannot be deleted.", "error")
        return redirect(url_for("manage_stages"))
    deals_using_it = models.Deal.query.filter_by(stage=stage.name).count()
    if deals_using_it > 0:
        flash(f'Cannot delete "{stage.name}" — {deals_using_it} deal(s) are currently using it. Move them to another stage first.')
        return redirect(url_for("manage_stages"))
    db.session.delete(stage)
    db.session.commit()
    flash(f'Stage "{stage.name}" was deleted.', "success")
    return redirect(url_for("manage_stages"))


# ---------------------------------------------------------------------------
# USERS
# ---------------------------------------------------------------------------
@app.route("/users")
@login_required
@role_required(*MANAGERS)
def users():
    all_users = models.User.query.order_by(models.User.name).all()
    return render_template("users_list.html", users=all_users)


@app.route("/users/add", methods=["GET", "POST"])
@login_required
@role_required(*MANAGERS)
def add_user():
    return user_editor()



@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(*MANAGERS)
def edit_user(user_id):
    return user_editor(models.User.query.get_or_404(user_id))



@app.route("/users/<int:user_id>/unlock", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def unlock_user(user_id):
    user = models.User.query.get_or_404(user_id)
    user.reset_failed_logins()
    db.session.commit()
    flash(f'"{user.name}" has been unlocked.', "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def delete_user(user_id):
    user = models.User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.")
        return redirect(url_for("users"))

    if user.role == "admin":
        admin_count = len(models.User.query.filter_by(role="admin").order_by(models.User.id).with_for_update().all())
        if admin_count <= 1:
            flash("Cannot delete the last remaining Admin account.")
            return redirect(url_for("users"))

    deal_count = models.Deal.query.filter_by(owner_id=user.id).count()
    lead_count = models.Lead.query.filter_by(assigned_rep_id=user.id).count()
    if deal_count > 0 or lead_count > 0:
        flash(f'Cannot delete "{user.name}" — they still own {deal_count} deal(s) and are assigned {lead_count} lead(s). Reassign these first.')
        return redirect(url_for("users"))

    models.PasswordResetToken.query.filter_by(user_id=user.id).delete()

    db.session.delete(user)
    db.session.commit()
    flash(f'"{user.name}" was deleted.', "success")
    return redirect(url_for("users"))




def deal_editor(deal=None):
    if request.method == "POST":
        try:
            company = record_field("company_id", models.Company, "company", True)
            contact = record_field("contact_id", models.Contact, "contact")
            if contact and contact.company_id != company.id:
                raise FormError("Choose a contact belonging to the selected company.")
            owner = sales_owner("owner_id")
            stage = choice_field("stage", get_stage_names(), "stage")
            value, err = parse_money(request.form.get("value"), "Deal value")
            if err: raise FormError(err)
            budget, err = parse_money(request.form.get("budget"), "Budget")
            if err: raise FormError(err)
            expected = date_field("close_date", "expected close date")
            actual = date_field("actual_close_date", "actual close date")
            requirements = text_field("requirements", "Requirements", 20000)
            target = deal or models.Deal(build_status="not_started")
            apply_stage(target, stage, actual, explicit_date=True)
            target.company_id, target.contact_id = company.id, contact.id if contact else None
            target.owner_id = owner.id if owner else None
            target.value, target.budget = value, budget
            target.close_date, target.requirements = expected, requirements
            db.session.add(target)
            db.session.commit()
            flash("Deal saved.", "success")
            return redirect(url_for("deal_detail", deal_id=target.id))
        except (FormError, IntegrityError) as error:
            form_problem(error)
    return render_template("deal_form.html", deal=deal,
        companies=models.Company.query.order_by(models.Company.name).all(),
        contacts=models.Contact.query.order_by(models.Contact.name).all(), users=sales_users(), stages=get_stage_names())


def lead_editor(lead=None):
    if request.method == "POST":
        try:
            name = text_field("prospect_name", "Prospect name", 120, True)
            email = email_field()
            phone = phone_field()
            if not email and not phone:
                raise FormError("Enter an email address or phone number so the prospect can be contacted.")
            company_name = text_field("company_name", "Company name", 150)
            company = record_field("company_id", models.Company, "company")
            contact = record_field("contact_id", models.Contact, "contact")
            if contact and (not company or contact.company_id != company.id):
                raise FormError("Choose a contact belonging to the selected company.")
            owner = sales_owner("assigned_rep_id")
            source = choice_field("source", LEAD_SOURCES, "lead source")
            status = request.form.get("status")
            if lead and lead.converted_deal:
                if status != "converted":
                    raise FormError("This lead has already been converted. Manage its linked deal instead.")
                if (company.id if company else None) != lead.company_id or (contact.id if contact else None) != lead.contact_id:
                    raise FormError("The converted company/contact links are fixed. Manage the linked deal for sales changes.")
            elif status not in LEAD_STATUSES:
                raise FormError("Choose a valid lead status.")
            target = lead or models.Lead()
            target.prospect_name, target.email, target.phone = name, email, phone
            target.company_name = company_name
            target.company_id = company.id if company else None
            target.contact_id = contact.id if contact else None
            target.assigned_rep_id = owner.id if owner else None
            target.source, target.status = source, status
            db.session.add(target)
            db.session.commit()
            flash("Lead saved.", "success")
            return redirect(url_for("lead_detail", lead_id=target.id))
        except (FormError, IntegrityError) as error:
            form_problem(error)
    return render_template("lead_form.html", lead=lead,
        companies=models.Company.query.order_by(models.Company.name).all(),
        contacts=models.Contact.query.order_by(models.Contact.name).all(), users=sales_users())


@app.route("/leads/<int:lead_id>/convert", methods=["GET", "POST"])
@login_required
@role_required(*SALES_EDITORS)
def convert_lead(lead_id):
    # A row lock serializes repeated conversion on MySQL; unique lead_id is a second guard.
    lead = models.Lead.query.filter_by(id=lead_id).with_for_update().first_or_404()
    check_lead_access(lead)
    if lead.converted_deal:
        check_deal_access(lead.converted_deal)
        flash("This lead is already linked to a deal.", "success")
        return redirect(url_for("deal_detail", deal_id=lead.converted_deal.id))
    if lead.status != "qualified":
        flash("Qualify the lead before converting it to a deal.", "error")
        return redirect(url_for("lead_detail", lead_id=lead.id))
    if request.method == "POST":
        try:
            company = record_field("company_id", models.Company, "company")
            new_name = text_field("new_company_name", "New company name", 150)
            if company and new_name:
                raise FormError("Select an existing company or enter a new company name, not both.")
            if not company and not new_name:
                raise FormError("Choose a company or enter the name of the company to create.")
            contact = record_field("contact_id", models.Contact, "contact")
            create_contact = request.form.get("create_contact") == "on"
            if contact and create_contact:
                raise FormError("Select an existing contact or create a contact, not both.")
            if contact and (not company or contact.company_id != company.id):
                raise FormError("The contact must belong to the selected company.")
            if create_contact and not lead.prospect_name:
                raise FormError("Add the prospect's name on the lead before creating a contact.")
            owner = sales_owner("owner_id")
            if not owner:
                raise FormError("Choose a sales owner for the converted deal.")
            stage = choice_field("stage", get_stage_names(), "stage")
            value, err = parse_money(request.form.get("value"), "Deal value")
            if err: raise FormError(err)
            budget, err = parse_money(request.form.get("budget"), "Budget")
            if err: raise FormError(err)
            expected = date_field("close_date", "expected close date")
            requirements = text_field("requirements", "Requirements", 20000)
            if not company:
                existing = models.Company.query.filter(db.func.lower(models.Company.name) == new_name.lower()).first()
                if existing:
                    raise FormError("That company already exists. Select it from the company list.")
                company = models.Company(name=new_name)
                db.session.add(company)
                db.session.flush()
            if create_contact:
                # Reuse an exact email match at this company instead of duplicating it.
                matches = models.Contact.query.filter(models.Contact.company_id == company.id,
                    db.func.lower(models.Contact.email) == lead.email.lower()).all() if lead.email else []
                if len(matches) > 1:
                    raise FormError("Several contacts use that email. Select the correct contact explicitly.")
                contact = matches[0] if matches else models.Contact(company_id=company.id,
                    name=lead.prospect_name, email=lead.email, phone=lead.phone)
                db.session.add(contact)
                db.session.flush()
            deal = models.Deal(company_id=company.id, contact_id=contact.id if contact else None,
                owner_id=owner.id, lead_id=lead.id, close_date=expected,
                value=value, budget=budget, requirements=requirements, build_status="not_started")
            apply_stage(deal, stage)
            db.session.add(deal)
            lead.company_id, lead.contact_id = company.id, contact.id if contact else None
            lead.assigned_rep_id, lead.status = owner.id, "converted"
            db.session.flush()
            db.session.add(models.Activity(related_type="Lead", related_id=lead.id, type="Note",
                notes=f"Converted to Deal #{deal.id}."))
            db.session.add(models.Activity(related_type="Deal", related_id=deal.id, type="Note",
                notes=f"Created from Lead #{lead.id}. The original activity history remains on the lead."))
            db.session.commit()
            flash("Lead converted. Its original history has been kept.", "success")
            return redirect(url_for("deal_detail", deal_id=deal.id))
        except (FormError, IntegrityError) as error:
            form_problem(error)
    return render_template("convert_lead.html", lead=lead,
        companies=models.Company.query.order_by(models.Company.name).all(),
        contacts=models.Contact.query.order_by(models.Contact.name).all(), users=sales_users(), stages=get_stage_names())


def company_editor(company=None):
    if request.method == "POST":
        try:
            name = text_field("name", "Company name", 150, True)
            query = models.Company.query.filter(db.func.lower(models.Company.name) == name.lower())
            if company: query = query.filter(models.Company.id != company.id)
            if query.first(): raise FormError("A company with that name already exists.")
            industry = text_field("industry", "Industry", 100)
            phone = phone_field()
            address = text_field("address", "Address", 255)
            target = company or models.Company()
            target.name, target.industry, target.phone, target.address = name, industry, phone, address
            db.session.add(target)
            db.session.commit()
            flash("Company saved.", "success")
            return redirect(url_for("company_detail", company_id=target.id))
        except (FormError, IntegrityError) as error: form_problem(error)
    return render_template("company_form.html", company=company)


def contact_editor(contact=None):
    if request.method == "POST":
        try:
            company = record_field("company_id", models.Company, "company", True)
            name = text_field("name", "Contact name", 120, True)
            email, phone = email_field(), phone_field()
            role = text_field("role_title", "Job title", 100)
            if contact and contact.company_id != company.id:
                used = models.Deal.query.filter_by(contact_id=contact.id).count() + models.Lead.query.filter_by(contact_id=contact.id).count()
                if used: raise FormError("This contact is linked to a lead or deal. Remove those links before changing its company.")
            target = contact or models.Contact()
            target.company_id, target.name, target.email, target.phone, target.role_title = company.id, name, email, phone, role
            db.session.add(target)
            db.session.commit()
            flash("Contact saved.", "success")
            return redirect(url_for("contact_detail", contact_id=target.id))
        except (FormError, IntegrityError) as error: form_problem(error)
    return render_template("contact_form.html", contact=contact,
        companies=models.Company.query.order_by(models.Company.name).all())


def user_editor(user=None):
    if request.method == "POST":
        try:
            name = text_field("name", "User name", 120, True)
            email = email_field(required=True)
            role = choice_field("role", ALL_ROLES, "role")
            duplicate = models.User.query.filter(db.func.lower(models.User.email) == email)
            if user: duplicate = duplicate.filter(models.User.id != user.id)
            if duplicate.first(): raise FormError("This email is already in use by another user.")
            # Serialize all role-changing writes against the same set of admin rows.
            admins = models.User.query.filter_by(role="admin").order_by(models.User.id).with_for_update().all()
            if user and user.role == "admin" and role != "admin" and len(admins) <= 1:
                raise FormError("The final Admin cannot be demoted. Create another Admin first.")
            if user and user.role in SALES_ROLES and role not in SALES_ROLES:
                if models.Deal.query.filter_by(owner_id=user.id).count() or models.Lead.query.filter_by(assigned_rep_id=user.id).count():
                    raise FormError("Reassign this user's leads and deals before changing them to a non-sales role.")
            password = request.form.get("password", "")
            if password or user is None:
                validate_password(password)
                if user and user.id == current_user.id:
                    raise FormError("Use Security to change your own password with your current password.")
            target = user or models.User(must_change_password=True)
            target.name, target.email, target.role = name, email, role
            if password:
                target.set_password(password)
                target.must_change_password = True
                target.reset_failed_logins()
                if user:
                    models.PasswordResetToken.query.filter_by(user_id=user.id, used=False).update({"used": True})
            db.session.add(target)
            db.session.commit()
            flash("User saved." + (" They must change their temporary password at next sign-in." if password else ""), "success")
            return redirect(url_for("my_dashboard" if target.id == current_user.id and role not in MANAGERS else "users"))
        except (FormError, IntegrityError) as error: form_problem(error)
    return render_template("user_form.html", user=user)


@app.route("/reminders/<int:reminder_id>/complete", methods=["POST"])
@login_required
@role_required(*SALES_EDITORS)
def complete_reminder(reminder_id):
    reminder = models.Reminder.query.get_or_404(reminder_id)
    check_reminder_access(reminder)
    if reminder.completed_at is None:
        reminder.completed_at = datetime.now()
        db.session.commit()
    flash("Reminder completed.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<int:reminder_id>/reopen", methods=["POST"])
@login_required
@role_required(*SALES_EDITORS)
def reopen_reminder(reminder_id):
    reminder = models.Reminder.query.get_or_404(reminder_id)
    check_reminder_access(reminder)
    reminder.completed_at = None
    db.session.commit()
    flash("Reminder reopened.", "success")
    return redirect(url_for("reminders"))


if __name__ == "__main__":
    app.run(debug=True)