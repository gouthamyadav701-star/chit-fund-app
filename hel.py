import os
import io
import logging
from datetime import datetime, timezone, date
from functools import wraps

from flask import Flask, render_template, redirect, url_for, flash, request, send_file, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail, Message
from wtforms import StringField, PasswordField, SubmitField, FloatField, SelectField, DateField, IntegerField
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp, Email
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Index
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from dotenv import load_dotenv
import openpyxl

# PDF optional - GTK lekapoina app crash avvakunda
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    WEASYPRINT_AVAILABLE = False
    HTML = None

# ───────────────────────────────
# CONFIG + LOGGING + SENTRY
# ───────────────────────────────

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

if dsn := os.environ.get("SENTRY_DSN"):
    sentry_sdk.init(dsn=dsn, integrations=[FlaskIntegration()], traces_sample_rate=0.2)

# IKKADA NEE FOLDER PERU MARCHUKO - example ki "template" pettanu
app = Flask(__name__, template_folder="template")  # <-- NI ISTAM: nee folder peru ikkada pettu
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-prod")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///chitfund.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["RATELIMIT_STORAGE_URI"] = os.environ.get("REDIS_URL", "memory://")
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "localhost")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 1025))
app.config["WTF_CSRF_TIME_LIMIT"] = None

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = "login"
limiter = Limiter(get_remote_address, app=app, default_limits=["500/hour"])
mail = Mail(app)

# ───────────────────────────────
# DECORATORS
# ───────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ───────────────────────────────
# MODELS + INDEXES + AUDIT TRAIL
# ───────────────────────────────

class AuditMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))
    deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))

class User(UserMixin, db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="viewer")
    __table_args__ = (Index("ix_user_username", "username"),)

class ChitGroup(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    months = db.Column(db.Integer, nullable=False)
    monthly_amount = db.Column(db.Numeric(10, 2), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    members = db.relationship("Member", backref="group", lazy="dynamic", cascade="all, delete-orphan")

class Member(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    paid_amount = db.Column(db.Numeric(10, 2), default=0)
    payments = db.relationship("Payment", backref="member", lazy="dynamic", cascade="all, delete-orphan")
    
    @property
    def balance(self):
        return round(float(self.total_amount - self.paid_amount), 2)
    
    __table_args__ = (
        Index("ix_member_group_id", "group_id"),
        Index("ix_member_phone", "phone"),
        Index("ix_member_deleted", "deleted"),
    )

class Payment(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    round_no = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        Index("ix_payment_member_id", "member_id"),
        Index("ix_payment_timestamp", "timestamp"),
    )

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ───────────────────────────────
# FORMS + VALIDATION
# ───────────────────────────────

class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")

class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    submit = SubmitField("Register")

class GroupForm(FlaskForm):
    name = StringField("Group Name", validators=[DataRequired(), Length(max=100)])
    total_amount = FloatField("Total Chit Amount", validators=[DataRequired(), NumberRange(min=1000)])
    months = IntegerField("Duration (months)", validators=[DataRequired(), NumberRange(min=1, max=60)])
    start_date = DateField("Start Date", validators=[DataRequired()])
    submit = SubmitField("Save Group")

class MemberForm(FlaskForm):
    group_id = SelectField("Chit Group", coerce=int, validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    phone = StringField("Phone", validators=[Optional(), Regexp(r'^\d{10}$', message="Enter 10 digit phone")])
    email = StringField("Email", validators=[Optional(), Email()])
    total_amount = FloatField("Total Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField("Save")

class PaymentForm(FlaskForm):
    amount = FloatField("Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    round_no = IntegerField("Round/Month No", validators=[Optional(), NumberRange(min=1)])
    submit = SubmitField("Add Payment")

# ───────────────────────────────
# AUTH ROUTES + RATE LIMIT
# ───────────────────────────────

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5/hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already taken", "danger")
            return render_template("register.html", form=form)
        
        role = "admin" if User.query.count() == 0 else "viewer"
        u = User(
            username=form.username.data, 
            password=generate_password_hash(form.password.data),
            role=role
        )
        db.session.add(u)
        db.session.commit()
        logging.info(f"New user registered: {u.username} as {role}")
        flash("Registered successfully. Please login", "success")
        return redirect(url_for("login"))
    return render_template("register.html", form=form)

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data, deleted=False).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user)
            logging.info(f"User {user.username} logged in")
            flash("Logged in successfully", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "danger")
        logging.warning(f"Failed login for {form.username.data}")
    return render_template("login.html", form=form)

@app.route("/logout")
@login_required
def logout():
    logging.info(f"User {current_user.username} logged out")
    logout_user()
    return redirect(url_for("login"))

# ───────────────────────────────
# DASHBOARD
# ───────────────────────────────

@app.route("/")
@login_required
def dashboard():
    groups = ChitGroup.query.filter_by(deleted=False).all()
    members = Member.query.filter_by(deleted=False).all()
    total_collected = sum(float(m.paid_amount) for m in members)
    total_expected = sum(float(m.total_amount) for m in members)
    return render_template("dashboard.html", groups=groups, members=members,
                           total_collected=total_collected, total_expected=total_expected)

# ───────────────────────────────
# CHIT GROUPS
# ───────────────────────────────

@app.route("/groups")
@login_required
def groups():
    data = ChitGroup.query.filter_by(deleted=False).all()
    return render_template("groups.html", groups=data)

@app.route("/groups/add", methods=["GET", "POST"])
@admin_required
def add_group():
    form = GroupForm()
    if form.validate_on_submit():
        monthly = round(form.total_amount.data / form.months.data, 2)
        g = ChitGroup(
            name=form.name.data,
            total_amount=form.total_amount.data,
            months=form.months.data,
            monthly_amount=monthly,
            start_date=form.start_date.data,
            created_by=current_user.id
        )
        db.session.add(g)
        db.session.commit()
        flash("Group created", "success")
        return redirect(url_for("groups"))
    return render_template("group_form.html", form=form)

# ───────────────────────────────
# MEMBERS + SOFT DELETE
# ───────────────────────────────

@app.route("/members")
@login_required
def members():
    data = Member.query.filter_by(deleted=False).order_by(Member.name).all()
    return render_template("members.html", members=data)

@app.route("/members/add", methods=["GET", "POST"])
@admin_required
def add_member():
    form = MemberForm()
    form.group_id.choices = [(g.id, g.name) for g in ChitGroup.query.filter_by(deleted=False)]
    if form.validate_on_submit():
        if form.phone.data and Member.query.filter_by(phone=form.phone.data, deleted=False).first():
            flash("Phone number already exists", "danger")
            return render_template("member_form.html", form=form)
        
        m = Member(
            group_id=form.group_id.data,
            name=form.name.data,
            phone=form.phone.data or None,
            email=form.email.data or None,
            total_amount=form.total_amount.data,
            created_by=current_user.id
        )
        db.session.add(m)
        db.session.commit()
        flash("Member added", "success")
        return redirect(url_for("members"))
    return render_template("member_form.html", form=form)

@app.route("/members/<int:mid>/delete", methods=["POST"])
@admin_required
def delete_member(mid):
    member = Member.query.get_or_404(mid)
    member.deleted = True
    member.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    logging.info(f"User {current_user.username} deleted member {mid}")
    flash("Member deleted", "warning")
    return redirect(url_for("members"))

# ───────────────────────────────
# PAYMENTS + INSTALLMENTS
# ───────────────────────────────

@app.route("/pay/<int:mid>", methods=["GET", "POST"])
@admin_required
def pay(mid):
    member = Member.query.filter_by(id=mid, deleted=False).first_or_404()
    form = PaymentForm()
    if form.validate_on_submit():
        amount = round(form.amount.data, 2)
        if amount > member.balance:
            flash(f"Overpayment not allowed. Balance is ₹{member.balance}", "danger")
            return render_template("pay.html", form=form, member=member)
        try:
            member.paid_amount = round(float(member.paid_amount) + amount, 2)
            p = Payment(
                member_id=mid, 
                amount=amount, 
                round_no=form.round_no.data,
                created_by=current_user.id
            )
            db.session.add(p)
            db.session.commit()
            logging.info(f"Payment {amount} for member {mid} by {current_user.username}")
            flash(f"Payment of ₹{amount} recorded", "success")
            if member.email:
                try:
                    msg = Message("Payment Received", recipients=[member.email])
                    msg.body = f"Hi {member.name}, we received ₹{amount}. Balance: ₹{member.balance}"
                    mail.send(msg)
                except Exception as e:
                    logging.error(f"Email failed: {e}")
            return redirect(url_for("dashboard"))
        except Exception as e:
            db.session.rollback()
            logging.error(f"Payment error: {e}")
            flash("Error recording payment", "danger")
    return render_template("pay.html", form=form, member=member)

# ───────────────────────────────
# REPORTS EXPORT
# ───────────────────────────────

@app.route("/export/excel")
@admin_required
def export_excel():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Members"
    ws.append(["Group", "Name", "Phone", "Total", "Paid", "Balance"])
    for m in Member.query.filter_by(deleted=False):
        ws.append([m.group.name, m.name, m.phone, float(m.total_amount), float(m.paid_amount), m.balance])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, download_name="chitfund_report.xlsx", as_attachment=True)

@app.route("/export/pdf")
@admin_required
def export_pdf():
    if not WEASYPRINT_AVAILABLE:
        flash("PDF export disabled. Install GTK3-Runtime to enable", "warning")
        return redirect(url_for("dashboard"))
    
    members = Member.query.filter_by(deleted=False).all()
    html = render_template("report.html", members=members, date=date.today())
    pdf = HTML(string=html).write_pdf()
    return send_file(io.BytesIO(pdf), download_name="chitfund_report.pdf", as_attachment=True)

# ───────────────────────────────
# HEALTH + CLI
# ───────────────────────────────

@app.route("/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.cli.command("init-db")
def init_db():
    """flask init-db"""
    db.create_all()
    print("Database tables created")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=os.environ.get("FLASK_DEBUG", "False") == "True")