from datetime import UTC, datetime

from flask import Blueprint, current_app, flash, redirect, render_template, send_file, url_for
from flask_login import current_user, login_required

from ..decorators import manager_required
from ..extensions import db
from ..forms import ChitGroupForm, EmptyForm, MemberForm, MembershipForm, RoundForm
from ..models import ChitGroup, GroupMembership, Member
from ..services import (
    archive_group_data,
    build_member_history_pdf,
    email_group_archive,
    ensure_group_completion_dates,
    enroll_member_in_group,
    generate_group_cycles,
    generate_installment_schedule,
    log_audit,
)

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
            db.session.flush()
            if form.group_id.data:
                selected_group = ChitGroup.query.get(form.group_id.data)
                if selected_group:
                    enroll_member_in_group(member, selected_group, current_user.id, is_primary=True, share_units=1.0)
            db.session.commit()
            flash(f"Member {member.name} added.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Member creation failed for %s", form.name.data)
            flash("Member could not be added.", "danger")

    return render_template("add_member.html", form=form)


@members_bp.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@manager_required
def edit_member(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    form = MemberForm(obj=member)
    groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
    form.group_id.choices = [(0, "No group")] + [(group.id, group.name) for group in groups]
    if not form.is_submitted():
        form.group_id.data = member.group_id or 0

    if form.validate_on_submit():
        try:
            existing_phone = (
                Member.query.filter(Member.id != member.id, Member.phone == form.phone.data, Member.deleted.is_(False)).first()
                if form.phone.data
                else None
            )
            if existing_phone:
                flash("Phone number already exists.", "danger")
                return render_template("add_member.html", form=form, is_edit=True, member=member)

            member.name = form.name.data.strip()
            member.email = (form.email.data or "").strip() or None
            member.phone = (form.phone.data or "").strip() or None
            member.total_amount = form.total_amount.data
            member.updated_by = current_user.id

            selected_group_id = form.group_id.data or None
            member.group_id = selected_group_id
            for membership in member.memberships:
                if not membership.deleted:
                    membership.is_primary = False

            if selected_group_id:
                selected_group = ChitGroup.query.get(selected_group_id)
                existing_membership = next(
                    (membership for membership in member.memberships if membership.group_id == selected_group_id and not membership.deleted),
                    None,
                )
                if existing_membership:
                    existing_membership.is_primary = True
                    existing_membership.updated_by = current_user.id
                elif selected_group:
                    membership = enroll_member_in_group(member, selected_group, current_user.id, is_primary=True, share_units=1.0)
                    membership.status = "Active"

            log_audit(current_user.id, "member.updated", "Member", member.id, {"name": member.name})
            db.session.commit()
            flash(f"Member {member.name} updated.", "success")
            return redirect(url_for("members.member_detail", member_id=member.id))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Member update failed for %s", member.id)
            flash("Member could not be updated.", "danger")

    return render_template("add_member.html", form=form, is_edit=True, member=member)


@members_bp.route("/members/<int:member_id>/delete", methods=["POST"])
@manager_required
def delete_member(member_id):
    form = EmptyForm()
    if form.validate_on_submit():
        member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
        member.deleted = True
        member.updated_by = current_user.id
        log_audit(current_user.id, "member.archived", "Member", member.id, {"name": member.name})
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
                auction_day=form.auction_day.data,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            generate_installment_schedule(group, current_user.id)
            generate_group_cycles(group, current_user.id)
            db.session.add(group)
            db.session.commit()
            flash(f"Group {group.name} created.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Group creation failed for %s", form.name.data)
            flash("Group could not be created.", "danger")

    return render_template("group_form.html", form=form)


@members_bp.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
@manager_required
def edit_group(group_id):
    group = ChitGroup.query.filter_by(id=group_id, deleted=False).first_or_404()
    form = ChitGroupForm(obj=group)

    if form.validate_on_submit():
        try:
            has_operational_history = any(not payment.deleted for payment in group.payments) or any(
                any(not bid.deleted for bid in cycle.bids) for cycle in group.cycles
            )
            locked_fields_changed = (
                float(group.monthly_amount) != float(form.monthly_amount.data)
                or int(group.total_members) != int(form.total_members.data)
                or group.start_date != form.start_date.data
            )
            if has_operational_history and locked_fields_changed:
                flash(
                    "This group already has payments or auction history, so monthly amount, total members, and start date cannot be changed.",
                    "warning",
                )
                return render_template("group_form.html", form=form, is_edit=True, group=group)

            group.name = form.name.data.strip()
            group.auction_day = form.auction_day.data
            if not has_operational_history:
                group.monthly_amount = form.monthly_amount.data
                group.total_members = int(form.total_members.data)
                group.start_date = form.start_date.data
                generate_installment_schedule(group, current_user.id)
                generate_group_cycles(group, current_user.id)

            group.updated_by = current_user.id
            log_audit(current_user.id, "group.updated", "ChitGroup", group.id, {"name": group.name})
            db.session.commit()
            flash(f"Group {group.name} updated.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Group update failed for %s", group.id)
            flash("Group could not be updated.", "danger")

    return render_template("group_form.html", form=form, is_edit=True, group=group)


@members_bp.route("/groups/<int:group_id>/advance", methods=["POST"])
@manager_required
def advance_round(group_id):
    form = RoundForm()
    if form.validate_on_submit():
        group = ChitGroup.query.filter_by(id=group_id, deleted=False).first_or_404()
        next_round = int(form.next_round.data)
        group.current_round = min(max(next_round, 1), group.total_members)
        for cycle in group.cycles:
            if cycle.cycle_number < group.current_round:
                cycle.status = "Closed" if cycle.status != "Closed" else cycle.status
            elif cycle.cycle_number == group.current_round and cycle.status == "Scheduled":
                cycle.status = "Open"
        group.updated_by = current_user.id
        log_audit(current_user.id, "group.round_advanced", "ChitGroup", group.id, {"round": group.current_round})
        db.session.commit()
        flash(f"{group.name} moved to round {group.current_round}.", "success")
    return redirect(url_for("core.dashboard"))


@members_bp.route("/groups/<int:group_id>/archive", methods=["POST"])
@manager_required
def archive_group(group_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        flash("Group archive request was invalid.", "danger")
        return redirect(url_for("core.dashboard"))

    group = ChitGroup.query.filter_by(id=group_id, deleted=False).first_or_404()
    ensure_group_completion_dates(group, current_user.id)

    if not group.completed_on:
        flash("This group can be deleted only after all 25 months are completed.", "warning")
        db.session.rollback()
        return redirect(url_for("core.dashboard"))

    try:
        exported = email_group_archive(group)
        if not exported:
            db.session.rollback()
            flash("Archive email could not be sent. Check admin email and mail settings before deleting.", "danger")
            return redirect(url_for("core.dashboard"))

        group.archive_export_sent_on = datetime.now(UTC).replace(tzinfo=None)
        archive_group_data(group, current_user.id)
        db.session.commit()
        flash(f"{group.name} archive emailed and group data deleted.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Manual group archive failed for %s", group.id)
        flash("Group could not be archived right now.", "danger")

    return redirect(url_for("core.dashboard"))


@members_bp.route("/members/<int:member_id>")
@login_required
def member_detail(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    membership_form = MembershipForm()
    groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
    membership_form.group_id.choices = [(group.id, group.name) for group in groups]
    return render_template("member_detail.html", member=member, membership_form=membership_form)


@members_bp.route("/members/<int:member_id>/memberships", methods=["POST"])
@manager_required
def add_membership(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    form = MembershipForm()
    groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
    form.group_id.choices = [(group.id, group.name) for group in groups]

    if form.validate_on_submit():
        group = ChitGroup.query.filter_by(id=form.group_id.data, deleted=False).first_or_404()
        membership = enroll_member_in_group(
            member,
            group,
            current_user.id,
            is_primary=not member.group_id,
            member_number=(form.member_number.data or "").strip() or None,
            share_units=form.share_units.data,
        )
        db.session.commit()
        flash(f"{member.name} added to {group.name} as slot {membership.slot_number}.", "success")
    else:
        flash("Could not add membership.", "danger")
    return redirect(url_for("members.member_detail", member_id=member.id))


@members_bp.route("/members/<int:member_id>/history-pdf")
@login_required
def member_history_pdf(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    pdf_file = build_member_history_pdf(member)
    safe_name = member.name.replace(" ", "_")
    return send_file(
        pdf_file,
        as_attachment=True,
        download_name=f"{safe_name}_payment_history.pdf",
        mimetype="application/pdf",
    )
