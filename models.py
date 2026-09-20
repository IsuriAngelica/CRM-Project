import secrets
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="account_executive")
    must_change_password = db.Column(db.Boolean, default=False)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_locked(self):
        return self.locked_until is not None and datetime.now() < self.locked_until

    def register_failed_login(self):
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1
        if self.failed_login_attempts >= 5:
            self.locked_until = datetime.now() + timedelta(minutes=15)
            self.failed_login_attempts = 0

    def reset_failed_logins(self):
        self.failed_login_attempts = 0
        self.locked_until = None


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)

    user = db.relationship("User", backref="reset_tokens")

    @staticmethod
    def generate_token():
        return secrets.token_urlsafe(32)

    def is_valid(self):
        return not self.used and datetime.now() < self.expires_at


class Stage(db.Model):
    __tablename__ = "stages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    industry = db.Column(db.String(100))
    phone = db.Column(db.String(30))
    address = db.Column(db.String(255))

    contacts = db.relationship("Contact", backref="company", lazy=True)
    leads = db.relationship("Lead", backref="company", lazy=True)
    deals = db.relationship("Deal", backref="company", lazy=True)


class Contact(db.Model):
    __tablename__ = "contacts"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role_title = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(30))

    deals = db.relationship("Deal", backref="primary_contact", lazy=True)


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    assigned_rep_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    prospect_name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    company_name = db.Column(db.String(150), nullable=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("contacts.id"), nullable=True)
    contact = db.relationship("Contact", backref="leads", lazy=True)
    source = db.Column(db.String(100))
    status = db.Column(db.String(50), default="new")
    created_at = db.Column(db.DateTime, default=datetime.now)

    assigned_rep = db.relationship("User", backref="leads", lazy=True)


    @property
    def display_name(self):
        return self.prospect_name or (self.company.name if self.company else None) or self.company_name or f"Lead #{self.id}"

    @property
    def organisation(self):
        return self.company.name if self.company else (self.company_name or "Not provided")


class Deal(db.Model):
    __tablename__ = "deals"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    contact_id = db.Column(db.Integer, db.ForeignKey("contacts.id"), nullable=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    stage = db.Column(db.String(50), default="new")
    value = db.Column(db.Numeric(12, 2))
    close_date = db.Column(db.Date, nullable=True)  # Expected close date (existing column).
    actual_close_date = db.Column(db.Date, nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True, unique=True)
    source_lead = db.relationship("Lead", backref=db.backref("converted_deal", uselist=False), lazy=True)
    requirements = db.Column(db.Text)
    budget = db.Column(db.Numeric(12, 2))

    # CRM-24: delivery build status
    build_status = db.Column(db.String(50), default="not_started")
    mismatch_flagged = db.Column(db.Boolean, default=False)
    mismatch_note = db.Column(db.Text)

    owner = db.relationship("User", backref="deals", lazy=True)


class Activity(db.Model):
    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    related_type = db.Column(db.String(50), nullable=False)
    related_id = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Reminder(db.Model):
    __tablename__ = "reminders"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)
    remind_at = db.Column(db.DateTime, nullable=False)
    message = db.Column(db.String(255))
    completed_at = db.Column(db.DateTime, nullable=True)

    deal = db.relationship("Deal", backref="reminders", lazy=True)
    lead = db.relationship("Lead", backref="reminders", lazy=True)