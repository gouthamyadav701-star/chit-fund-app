from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user

from ..decorators import manager_required
from ..extensions import db
from ..forms import ChitGroupForm, EmptyForm, MemberForm, RoundForm
from ..models import ChitGroup, Member
from ..services import generate_installment_schedule

members_bp = Blueprint("members", __name__)


@members_bp.route("/members/add", methods=["GET", "POST"])
@manager_required
def add_member():
    form = MemberForm()
    groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
    form.group_id.choices = [(0, "No group")] + [(group.id, group.name) for group in groups]

    if form.validate_on_submit():
        try:
            if form.phone.data and Member.query.filter_by(phone=form.phone.data, deleted=False).first():
                flash("Phone number already exists.", "danger")
                return render_template("add_member.html", form=form)

            member = Member(
                name=form.name.data.strip(),
                email=(form.email.data or "").strip() or None,
                phone=(form.phone.data or "").strip() or None,
                total_amount=form.total_amount.data,
                group_id=form.group_id.data or None,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            db.session.add(member)
            db.session.commit()
            flash(f"Member {member.name} added.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Member creation failed for %s", form.name.data)
            flash("Member could not be added.", "danger")

    return render_template("add_member.html", form=form)


@members_bp.route("/members/<int:member_id>/delete", methods=["POST"])
@manager_required
def delete_member(member_id):
    form = EmptyForm()
    if form.validate_on_submit():
        member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
        member.deleted = True
        member.updated_by = current_user.id
        db.session.commit()
        flash(f"{member.name} was archived.", "success")
    return redirect(url_for("core.dashboard"))


@members_bp.route("/groups", methods=["GET", "POST"])
@manager_required
def create_group():
    form = ChitGroupForm()
    if form.validate_on_submit():
        try:
            group = ChitGroup(
                name=form.name.data.strip(),
                monthly_amount=form.monthly_amount.data,
                total_members=int(form.total_members.data),
                start_date=form.start_date.data,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            generate_installment_schedule(group, current_user.id)
            db.session.add(group)
            db.session.commit()
            flash(f"Group {group.name} created.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Group creation failed for %s", form.name.data)
            flash("Group could not be created.", "danger")

    return render_template("group_form.html", form=form)


@members_bp.route("/groups/<int:group_id>/advance", methods=["POST"])
@manager_required
def advance_round(group_id):
    form = RoundForm()
    if form.validate_on_submit():
        group = ChitGroup.query.filter_by(id=group_id, deleted=False).first_or_404()
        next_round = int(form.next_round.data)
        group.current_round = min(max(next_round, 1), group.total_members)
        group.updated_by = current_user.id
        db.session.commit()
        flash(f"{group.name} moved to round {group.current_round}.", "success")
    return redirect(url_for("core.dashboard"))
