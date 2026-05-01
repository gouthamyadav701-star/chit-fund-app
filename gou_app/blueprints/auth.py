from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_
from werkzeug.security import check_password_hash

from ..decorators import admin_required
from ..extensions import bcrypt, db, limiter
from ..forms import EmptyForm, LoginForm, RecoverBusinessCodeForm, RegisterForm
from ..models import Business, Member, User
from ..tenant import generate_business_code, normalize_business_code

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            business_code = normalize_business_code(form.business_code.data or "")
            if form.account_type.data == "Customer":
                phone = (form.phone.data or "").strip()
                business = Business.query.filter_by(code=business_code, deleted=False).first()
                if not business:
                    flash("Business code not found.", "danger")
                    return render_template("register.html", form=form)
                member = Member.query.filter_by(phone=phone, business_id=business.id, deleted=False).first()
                if not member:
                    flash("Phone number not found in this business records. Contact office/admin first.", "danger")
                    return render_template("register.html", form=form)
                if User.query.filter_by(member_id=member.id, business_id=business.id, deleted=False).first():
                    flash("An account already exists for this member.", "warning")
                    return render_template("register.html", form=form)

                username = phone
                email = ((form.email.data or "").strip().lower() or f"{phone.replace('+', '')}@member.local")

                if User.query.filter_by(username=username, business_id=business.id).first():
                    flash("An account already exists with this phone number.", "danger")
                    return render_template("register.html", form=form)
                if User.query.filter_by(email=email, business_id=business.id).first():
                    email = f"{phone.replace('+', '')}.{member.id}@member.local"
                    if User.query.filter_by(email=email, business_id=business.id).first():
                        flash("An account already exists for this member contact.", "danger")
                        return render_template("register.html", form=form)

                new_user = User(
                    business_id=business.id,
                    username=username,
                    email=email,
                    password_hash=bcrypt.generate_password_hash(form.password.data).decode("utf-8"),
                    role="Customer",
                    is_approved=True,
                    member_id=member.id,
                )
                success_message = "Customer account created. Please log in."
            else:
                business_name = (form.business_name.data or "").strip()
                username = (form.username.data or "").strip()
                email = (form.email.data or "").strip().lower()

                if business_code:
                    business = Business.query.filter_by(code=business_code, deleted=False).first()
                    if not business:
                        flash("Business code not found.", "danger")
                        return render_template("register.html", form=form)
                    if User.query.filter_by(username=username, business_id=business.id).first():
                        flash("Username already exists in this business.", "danger")
                        return render_template("register.html", form=form)
                    if User.query.filter_by(email=email, business_id=business.id).first():
                        flash("Email already exists in this business.", "danger")
                        return render_template("register.html", form=form)
                    role = form.role.data
                    is_approved = False
                    success_message = "Staff account created. Wait for business admin approval."
                else:
                    business = Business(
                        name=business_name,
                        code=generate_business_code(business_name),
                    )
                    db.session.add(business)
                    db.session.flush()
                    role = "Admin"
                    is_approved = True
                    success_message = f"Business created. Your business code is {business.code}. Please log in."

                new_user = User(
                    business_id=business.id,
                    username=username,
                    email=email,
                    password_hash=bcrypt.generate_password_hash(form.password.data).decode("utf-8"),
                    role=role,
                    is_approved=is_approved,
                )

            db.session.add(new_user)
            db.session.commit()
            flash(success_message, "success")
            return redirect(url_for("auth.login"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Registration failed for %s", form.username.data or form.phone.data)
            flash("Registration failed. Please try again.", "danger")

    return render_template("register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        business_code = normalize_business_code(form.business_code.data)
        business = Business.query.filter_by(code=business_code, deleted=False).first()
        if not business:
            flash("Business code not found.", "danger")
            return render_template("login.html", form=form)

        identifier = form.username.data.strip()
        user = User.query.filter_by(username=identifier, business_id=business.id, deleted=False).first()
        if not user:
            member = Member.query.filter_by(phone=identifier, business_id=business.id, deleted=False).first()
            if member:
                user = User.query.filter_by(member_id=member.id, business_id=business.id, deleted=False).first()
        password_ok = False
        if user:
            try:
                password_ok = bcrypt.check_password_hash(user.password_hash, form.password.data)
            except ValueError:
                password_ok = check_password_hash(user.password_hash, form.password.data)

        if not user or not password_ok:
            current_app.logger.warning("Failed login for %s from %s", form.username.data, request.remote_addr)
            flash("Invalid credentials.", "danger")
            return render_template("login.html", form=form)

        if not user.is_approved:
            flash("Your account is waiting for admin approval.", "warning")
            return render_template("login.html", form=form)

        login_user(user)
        current_app.logger.info("User %s logged in", user.username)
        flash("Logged in successfully.", "success")
        return redirect(url_for("core.dashboard"))

    return render_template("login.html", form=form)


@auth_bp.route("/forgot-business-code", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def forgot_business_code():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = RecoverBusinessCodeForm()
    matches: list[Business] = []
    submitted_identifier = ""

    if form.validate_on_submit():
        submitted_identifier = (form.identifier.data or "").strip()
        lowered_identifier = submitted_identifier.lower()

        users = User.query.filter(
            User.deleted.is_(False),
            or_(
                User.username == submitted_identifier,
                User.email == lowered_identifier,
            ),
        ).all()

        phone_match_members = Member.query.filter_by(phone=submitted_identifier, deleted=False).all()
        phone_member_ids = [member.id for member in phone_match_members]
        if phone_member_ids:
            customer_users = User.query.filter(
                User.deleted.is_(False),
                User.member_id.in_(phone_member_ids),
            ).all()
            users.extend(customer_users)

        business_ids = sorted({user.business_id for user in users if user.business_id})
        if business_ids:
            matches = Business.query.filter(Business.id.in_(business_ids), Business.deleted.is_(False)).order_by(Business.name.asc()).all()
        else:
            flash("No business code found for that username, email, or phone number.", "warning")

    return render_template("forgot_business_code.html", form=form, matches=matches, submitted_identifier=submitted_identifier)


@auth_bp.route("/logout")
@login_required
def logout():
    current_app.logger.info("User %s logged out", current_user.username)
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve_user(user_id):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(id=user_id, business_id=current_user.business_id, deleted=False).first_or_404()
        user.is_approved = True
        user.updated_by = current_user.id
        db.session.commit()
        flash(f"Approved {user.username}.", "success")
    return redirect(url_for("core.dashboard"))


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(id=user_id, business_id=current_user.business_id, deleted=False).first_or_404()
        if user.id == current_user.id:
            flash("You cannot delete your own account.", "danger")
            return redirect(url_for("core.dashboard"))
        user.deleted = True
        user.updated_by = current_user.id
        db.session.commit()
        flash(f"Deactivated {user.username}.", "success")
    return redirect(url_for("core.dashboard"))
