from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from ..decorators import manager_required
from ..extensions import db
from ..forms import ChitGroupForm, EmptyForm, ExistingGroupSetupForm, MemberForm, MembershipForm, RoundForm
from ..models import ChitGroup, GroupMembership, Member
from ..services import (
    assign_cycle_winner,
    archive_group_data,
    build_member_history_pdf,
    create_opening_balance_payment,
    email_group_archive,
    ensure_group_completion_dates,
    enroll_member_in_group,
    generate_group_cycles,
    generate_installment_schedule,
    log_audit,
    recalculate_member_financials,
)

members_bp = Blueprint("members", __name__)


def _ensure_staff_access():
    if current_user.role == "Customer":
        abort(403)


@members_bp.route("/members/add", methods=["GET", "POST"])
@manager_required
def add_member():
    form = MemberForm()
    groups = ChitGroup.query.filter_by(deleted=False, business_id=current_user.business_id).order_by(ChitGroup.name).all()
    form.group_id.choices = [(0, "No group")] + [(group.id, group.name) for group in groups]

    if form.validate_on_submit():
        try:
            if form.phone.data and Member.query.filter_by(phone=form.phone.data, business_id=current_user.business_id, deleted=False).first():
                flash("Phone number already exists.", "danger")
                return render_template("add_member.html", form=form)

            member = Member(
                business_id=current_user.business_id,
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
                selected_group = ChitGroup.query.filter_by(id=form.group_id.data, business_id=current_user.business_id, deleted=False).first()
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
    member = Member.query.filter_by(id=member_id, business_id=current_user.business_id, deleted=False).first_or_404()
    form = MemberForm(obj=member)
    groups = ChitGroup.query.filter_by(deleted=False, business_id=current_user.business_id).order_by(ChitGroup.name).all()
    form.group_id.choices = [(0, "No group")] + [(group.id, group.name) for group in groups]
    if not form.is_submitted():
        form.group_id.data = member.group_id or 0

    if form.validate_on_submit():
        try:
            existing_phone = None
            if form.phone.data:
                existing_phone = Member.query.filter(
                    Member.id != member.id,
                    Member.business_id == current_user.business_id,
                    Member.phone == form.phone.data,
                    Member.deleted.is_(False),
                ).first()
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
                selected_group = ChitGroup.query.filter_by(id=selected_group_id, business_id=current_user.business_id, deleted=False).first()
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
        member = Member.query.filter_by(id=member_id, business_id=current_user.business_id, deleted=False).first_or_404()
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
                business_id=current_user.business_id,
                name=form.name.data.strip(),
                monthly_amount=form.monthly_amount.data,
                total_members=int(form.total_members.data),
                start_date=form.start_date.data,
                current_round=1,
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


@members_bp.route("/groups/list")
@login_required
def group_list():
    _ensure_staff_access()
    groups = (
        ChitGroup.query.filter_by(deleted=False, business_id=current_user.business_id)
        .order_by(ChitGroup.name, ChitGroup.start_date)
        .all()
    )
    return render_template("group_list.html", groups=groups)


@members_bp.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id):
    _ensure_staff_access()
    group = ChitGroup.query.filter_by(id=group_id, business_id=current_user.business_id, deleted=False).first_or_404()
    memberships = sorted(
        [membership for membership in group.memberships if not membership.deleted],
        key=lambda item: (item.status != "Active", item.slot_number, item.member.name.lower()),
    )
    current_cycle = next(
        (cycle for cycle in group.cycles if not cycle.deleted and cycle.cycle_number == group.current_round),
        None,
    )
    winner_cycles = [
        cycle
        for cycle in group.cycles
        if not cycle.deleted and cycle.status == "Closed" and cycle.winner_membership and cycle.winner_membership.member
    ]
    return render_template(
        "group_detail.html",
        group=group,
        memberships=memberships,
        current_cycle=current_cycle,
        winner_cycles=winner_cycles,
    )


@members_bp.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
@manager_required
def edit_group(group_id):
    group = ChitGroup.query.filter_by(id=group_id, business_id=current_user.business_id, deleted=False).first_or_404()
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


@members_bp.route("/groups/<int:group_id>/existing-setup", methods=["GET", "POST"])
@manager_required
def existing_group_setup(group_id):
    group = ChitGroup.query.filter_by(id=group_id, business_id=current_user.business_id, deleted=False).first_or_404()
    memberships = sorted(
        [membership for membership in group.memberships if not membership.deleted and membership.status == "Active"],
        key=lambda item: (item.member.name.lower(), item.slot_number),
    )
    form = ExistingGroupSetupForm()
    if not form.is_submitted():
        form.current_round.data = group.current_round

    has_live_activity = any(
        not payment.deleted and payment.status != "Opening" for payment in group.payments
    ) or any(any(not bid.deleted for bid in cycle.bids) for cycle in group.cycles)

    if form.validate_on_submit():
        if has_live_activity:
            flash(
                "Existing group setup is only allowed before normal live payments or auction entries begin for this group.",
                "warning",
            )
            return render_template(
                "existing_group_setup.html",
                form=form,
                group=group,
                memberships=memberships,
                has_live_activity=has_live_activity,
            )

        try:
            current_round = min(max(int(form.current_round.data), 1), group.total_members)

            for payment in group.payments:
                if not payment.deleted and payment.status == "Opening":
                    payment.deleted = True
                    payment.updated_by = current_user.id

            for cycle in group.cycles:
                cycle.winner_membership = None
                cycle.winning_bid_amount = None
                cycle.discount_amount = 0
                cycle.dividend_per_member = 0
                cycle.collected_amount = 0
                cycle.penalty_total = 0
                cycle.updated_by = current_user.id
                cycle.status = "Closed" if cycle.cycle_number < current_round else "Scheduled"
                if cycle.cycle_number == current_round:
                    cycle.status = "Open"
                for bid in cycle.bids:
                    bid.deleted = True
                    bid.updated_by = current_user.id

            touched_members: dict[int, Member] = {}
            manual_dividends: dict[int, float] = {}
            for membership in memberships:
                touched_members[membership.member.id] = membership.member
                opening_paid = float((request.form.get(f"opening_paid_{membership.id}") or "0").strip() or 0)
                months_paid = int((request.form.get(f"months_paid_{membership.id}") or "0").strip() or 0)
                arrears = float((request.form.get(f"arrears_{membership.id}") or "0").strip() or 0)
                penalty = float((request.form.get(f"penalty_{membership.id}") or "0").strip() or 0)
                dividend = float((request.form.get(f"dividend_{membership.id}") or "0").strip() or 0)
                won_cycle_number = int((request.form.get(f"won_cycle_{membership.id}") or "0").strip() or 0)
                payout_amount = float((request.form.get(f"payout_{membership.id}") or "0").strip() or 0)

                if opening_paid <= 0 and months_paid > 0:
                    opening_paid = round(months_paid * membership.expected_amount, 2)

                membership.arrears_balance = round(max(arrears, 0), 2)
                membership.penalty_balance = round(max(penalty, 0), 2)
                membership.updated_by = current_user.id
                manual_dividends[membership.id] = round(max(dividend, 0), 2)

                if opening_paid > 0:
                    create_opening_balance_payment(membership.member, membership, round(opening_paid, 2), current_user.id)

                if won_cycle_number > 0:
                    cycle = next((item for item in group.cycles if item.cycle_number == won_cycle_number), None)
                    if cycle and cycle.cycle_number < current_round and payout_amount > 0:
                        assign_cycle_winner(cycle, membership, round(payout_amount, 2), current_user.id, note="Imported opening setup")

            for membership in memberships:
                if manual_dividends.get(membership.id, 0) > 0:
                    membership.total_dividend = manual_dividends[membership.id]

            group.current_round = current_round
            group.completed_on = None
            group.retention_expires_on = None
            group.archive_export_sent_on = None
            group.archived_on = None
            group.updated_by = current_user.id

            for member in touched_members.values():
                recalculate_member_financials(member)

            log_audit(current_user.id, "group.opening_setup_saved", "ChitGroup", group.id, {"round": group.current_round})
            db.session.commit()
            flash(f"Existing group setup saved for {group.name}. You can continue from month {group.current_round}.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Existing group setup failed for %s", group.id)
            flash("Existing group setup could not be saved.", "danger")

    return render_template(
        "existing_group_setup.html",
        form=form,
        group=group,
        memberships=memberships,
        has_live_activity=has_live_activity,
    )


@members_bp.route("/groups/<int:group_id>/advance", methods=["POST"])
@manager_required
def advance_round(group_id):
    form = RoundForm()
    if form.validate_on_submit():
        group = ChitGroup.query.filter_by(id=group_id, business_id=current_user.business_id, deleted=False).first_or_404()
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

    group = ChitGroup.query.filter_by(id=group_id, business_id=current_user.business_id, deleted=False).first_or_404()
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
    if current_user.role == "Customer" and current_user.member_id != member_id:
        abort(403)
    member = Member.query.filter_by(id=member_id, business_id=current_user.business_id, deleted=False).first_or_404()
    membership_form = MembershipForm()
    groups = ChitGroup.query.filter_by(deleted=False, business_id=current_user.business_id).order_by(ChitGroup.name).all()
    membership_form.group_id.choices = [(group.id, group.name) for group in groups]
    return render_template("member_detail.html", member=member, membership_form=membership_form)


@members_bp.route("/members/<int:member_id>/memberships", methods=["POST"])
@manager_required
def add_membership(member_id):
    if current_user.role == "Customer" and current_user.member_id != member_id:
        abort(403)
    member = Member.query.filter_by(id=member_id, business_id=current_user.business_id, deleted=False).first_or_404()
    form = MembershipForm()
    groups = ChitGroup.query.filter_by(deleted=False, business_id=current_user.business_id).order_by(ChitGroup.name).all()
    form.group_id.choices = [(group.id, group.name) for group in groups]

    if form.validate_on_submit():
        group = ChitGroup.query.filter_by(id=form.group_id.data, business_id=current_user.business_id, deleted=False).first_or_404()
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
    if current_user.role == "Customer" and current_user.member_id != member_id:
        abort(403)
    member = Member.query.filter_by(id=member_id, business_id=current_user.business_id, deleted=False).first_or_404()
    pdf_file = build_member_history_pdf(member)
    safe_name = member.name.replace(" ", "_")
    return send_file(
        pdf_file,
        as_attachment=True,
        download_name=f"{safe_name}_payment_history.pdf",
        mimetype="application/pdf",
    )
