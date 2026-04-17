from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..decorators import admin_required
from ..extensions import db, limiter
from ..forms import EmptyForm, LoginForm, RegisterForm
from ..models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            if User.query.filter_by(username=form.username.data).first():
                flash("Username already exists.", "danger")
                return render_template("register.html", form=form)
            if User.query.filter_by(email=form.email.data).first():
                flash("Email already exists.", "danger")
                return render_template("register.html", form=form)

            first_user = User.query.count() == 0
            new_user = User(
                username=form.username.data.strip(),
                email=form.email.data.strip().lower(),
                password_hash=generate_password_hash(form.password.data),
                role="Admin" if first_user else form.role.data,
                is_approved=first_user,
            )
            db.session.add(new_user)
            db.session.commit()
            flash("Admin account created. Please log in." if first_user else "Registered. Wait for admin approval.", "success")
            return redirect(url_for("auth.login"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Registration failed for %s", form.username.data)
            flash("Registration failed. Please try again.", "danger")

    return render_template("register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip(), deleted=False).first()
        if not user or not check_password_hash(user.password_hash, form.password.data):
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
        user = User.query.get_or_404(user_id)
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
        user = User.query.get_or_404(user_id)
        if user.id == current_user.id:
            flash("You cannot delete your own account.", "danger")
            return redirect(url_for("core.dashboard"))
        user.deleted = True
        user.updated_by = current_user.id
        db.session.commit()
        flash(f"Deactivated {user.username}.", "success")
    return redirect(url_for("core.dashboard"))
