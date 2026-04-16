import os
import io
import re
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import FlaskForm
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from wtforms import StringField, PasswordField, SubmitField, FloatField, SelectField
from wtforms.validators import DataRequired, Length, Optional, NumberRange, Regexp, EqualTo
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Index
from dotenv import load_dotenv
from fpdf import FPDF

# ───────────────────────────────
# CONFIG
# ───────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# IST Timezone - India Standard Time
IST = timezone(timedelta(hours=5, minutes=30))

app = Flask(__name__, template_folder="templates")
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///chit_fund.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['RATELIMIT_STORAGE_URI'] = os.environ.get("REDIS_URL", "memory://")
app.config["WTF_CSRF_TIME_LIMIT"] = None

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
limiter = Limiter(get_remote_address, app=app, default_limits=["500/hour"])

# ───────────────────────────────
# DECORATORS
# ───────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'Admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ───────────────────────────────
# MODELS + AUDIT - IST lo save chestam
# ───────────────────────────────
class AuditMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(IST))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(IST))
    deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True))

class User(UserMixin, db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='Viewer')
    is_approved = db.Column(db.Boolean, default=False)
    __table_args__ = (Index("ix_user_username", "username"),)

class Member(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    paid_amount = db.Column(db.Numeric(10, 2), default=0.0)
    payments = db.relationship("Payment", backref="member", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def due_amount(self):
        return round(float(self.total_amount - self.paid_amount), 2)
    
    __table_args__ = (
        Index("ix_member_phone", "phone"),
        Index("ix_member_deleted", "deleted"),
    )

class Payment(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(IST))  # IST lo save
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
def validate_password_custom(form, field):
    password = field.data
    if len(password) < 8:
        raise ValueError("Min 8 characters required")
    if not re.search(r"\d", password):
        raise ValueError("Must contain a number")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Must contain uppercase")

class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), validate_password_custom])
    confirm_password = PasswordField("Confirm Password", validators=[DataRequired(), EqualTo('password', message='Passwords must match')])
    role = SelectField("Role", choices=[('Viewer', 'Viewer'), ('Admin', 'Admin')], validators=[DataRequired()])
    submit = SubmitField("Register")

class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")

class MemberForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    phone = StringField("Phone", validators=[Optional(), Regexp(r'^\d{10}$', message="Enter 10 digit phone")])
    total_amount = FloatField("Total Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField("Add Member")

class PaymentForm(FlaskForm):
    amount = FloatField("Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField("Add Payment")

# ───────────────────────────────
# ROUTES
# ───────────────────────────────
@app.route('/')
@login_required
def dashboard():
    members = Member.query.filter_by(deleted=False).all()
    total_paid = sum(float(m.paid_amount) for m in members)
    total_due = sum(m.due_amount for m in members)
    return render_template('dashboard.html', members=members, paid=total_paid, due=total_due)

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5/hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already exists", "danger")
            return render_template('register.html', form=form)

        hashed_pw = generate_password_hash(form.password.data)
        is_first = User.query.count() == 0
        new_user = User(
            username=form.username.data, 
            password=hashed_pw, 
            role='Admin' if is_first else form.role.data,
            is_approved=is_first
        )
        db.session.add(new_user)
        db.session.commit()
        logging.info(f"New user registered: {new_user.username}")
        flash("Registered! Wait for admin approval" if not is_first else "Admin account created. Please login", "success")
        return redirect(url_for('login'))

    return render_template('register.html', form=form)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10/minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data, deleted=False).first()
        if user and check_password_hash(user.password, form.password.data):
            if not user.is_approved:
                flash("Waiting for admin approval", "warning")
            else:
                login_user(user)
                logging.info(f"User {user.username} logged in")
                flash("Logged in successfully", "success")
                return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials", "danger")
            logging.warning(f"Failed login for {form.username.data}")

    return render_template('login.html', form=form)

@app.route('/add_member', methods=['GET', 'POST'])
@admin_required
def add_member():
    form = MemberForm()
    if form.validate_on_submit():
        if form.phone.data and Member.query.filter_by(phone=form.phone.data, deleted=False).first():
            flash("Phone number already exists", "danger")
            return render_template('add_member.html', form=form)
        
        new_member = Member(
            name=form.name.data,
            phone=form.phone.data or None,
            total_amount=form.total_amount.data,
            created_by=current_user.id
        )
        db.session.add(new_member)
        db.session.commit()
        flash(f"Member {form.name.data} added", "success")
        return redirect(url_for('dashboard'))
    
    return render_template('add_member.html', form=form)

@app.route('/payment/<int:member_id>', methods=['GET', 'POST'])
@admin_required
def make_payment(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    form = PaymentForm()
    
    if form.validate_on_submit():
        amount = round(form.amount.data, 2)
        if amount > member.due_amount:
            flash(f"Overpayment not allowed. Due is ₹{member.due_amount}", "danger")
            return render_template('payment.html', form=form, member=member)
        
        try:
            payment = Payment(member_id=member_id, amount=amount, created_by=current_user.id)
            member.paid_amount = round(float(member.paid_amount) + amount, 2)
            db.session.add(payment)
            db.session.commit()
            logging.info(f"Payment {amount} for member {member_id} by {current_user.username}")
            flash(f"Payment Rs {amount} added for {member.name}", "success")
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            logging.error(f"Payment error: {e}")
            flash("Error in payment", "danger")
    
    return render_template('payment.html', form=form, member=member)

@app.route('/history')
@login_required
def history():
    payments = Payment.query.filter_by(deleted=False).order_by(Payment.timestamp.desc()).all()
    return render_template('history.html', payments=payments)

@app.route('/receipt/<int:payment_id>')
@login_required
def receipt(payment_id):
    pay = Payment.query.filter_by(id=payment_id, deleted=False).first_or_404()
    member = Member.query.get(pay.member_id)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 18)
    pdf.cell(200, 10, txt="CHIT FUND RECEIPT", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Member Name: {member.name}", ln=True)
    pdf.cell(200, 10, txt=f"Member ID: {member.id}", ln=True)
    pdf.cell(200, 10, txt=f"Amount Paid: Rs {float(pay.amount):.2f}", ln=True)
    
    # IST lo save chesam kabatti direct vadey
    formatted_date = pay.timestamp.strftime("%d-%m-%Y %I:%M %p")
    pdf.cell(200, 10, txt=f"Date: {formatted_date}", ln=True)
    
    pdf.ln(10)
    pdf.set_font("Arial", "I", 10)
    pdf.cell(200, 10, txt="Thank you for your payment!", ln=True, align='C')

    pdf_bytes = pdf.output(dest='S').encode('latin1')
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    return send_file(
        buf, 
        download_name=f"receipt_{payment_id}.pdf", 
        as_attachment=True,
        mimetype='application/pdf'
    )

@app.route('/logout')
@login_required
def logout():
    logging.info(f"User {current_user.username} logged out")
    logout_user()
    return redirect(url_for('login'))

# ───────────────────────────────
# MAIN
# ───────────────────────────────
with app.app_context():
    db.create_all()
    if not User.query.first():
        admin = User(
            username='admin',
            password=generate_password_hash('Admin@123'),
            role='Admin',
            is_approved=True
        )
        db.session.add(admin)
        db.session.commit()
        logging.info("Default admin created: admin / Admin@123")

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))