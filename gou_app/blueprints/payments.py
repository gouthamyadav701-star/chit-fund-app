from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from ..decorators import manager_required
from ..extensions import db
from ..forms import PaymentFilterForm, PaymentForm
from ..models import ChitGroup, GroupMembership, Member, Payment
from ..services import build_payment_excel, build_receipt_pdf, create_payment, pending_memberships, queue_payment_notifications

payments_bp = Blueprint("payments", __name__)


@payments_bp.route("/payments/<int:member_id>/new", methods=["GET", "POST"])
@manager_required
def make_payment(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    form = PaymentForm()
    active_memberships = [membership for membership in member.active_memberships if membership.group and not membership.deleted]
    form.membership_id.choices = [(membership.id, f"{membership.display_label} - {membership.payment_status}") for membership in active_memberships]

    if form.validate_on_submit():
        membership = GroupMembership.query.filter_by(id=form.membership_id.data, deleted=False).first_or_404()
        payment = create_payment(member, membership, round(form.amount.data, 2), current_user.id)
        db.session.commit()
        queue_payment_notifications(member, payment)
        flash(f"Payment of Rs {float(payment.amount):.2f} added for {member.name}.", "success")
        return redirect(url_for("core.dashboard"))

    return render_template("payment.html", form=form, member=member)


@payments_bp.route("/payments/history")
@login_required
def history():
    form = PaymentFilterForm(request.args)
    members = Member.query.filter_by(deleted=False).order_by(Member.name).all()
    groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
    form.member_id.choices = [(0, "All Members")] + [(member.id, member.name) for member in members]
    form.group_id.choices = [(0, "All Groups")] + [(group.id, group.name) for group in groups]

    query = Payment.query.filter_by(deleted=False)
    if form.member_id.data:
        query = query.filter_by(member_id=form.member_id.data)
    if form.group_id.data:
        query = query.filter_by(group_id=form.group_id.data)
    if form.status.data:
        query = query.filter_by(status=form.status.data)
    if form.date_from.data:
        query = query.filter(Payment.timestamp >= form.date_from.data)
    if form.date_to.data:
        query = query.filter(Payment.timestamp <= form.date_to.data)

    payments = query.order_by(Payment.timestamp.desc()).all()
    defaulters = pending_memberships(form.group_id.data or None)
    return render_template("history.html", payments=payments, defaulters=defaulters, filter_form=form)


@payments_bp.route("/payments/export")
@login_required
def export_excel():
    payments = Payment.query.filter_by(deleted=False).order_by(Payment.timestamp.desc()).all()
    workbook = build_payment_excel(payments)
    return send_file(
        workbook,
        as_attachment=True,
        download_name="payments.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@payments_bp.route("/payments/receipt/<int:payment_id>")
@login_required
def receipt(payment_id):
    payment = Payment.query.filter_by(id=payment_id, deleted=False).first_or_404()
    pdf_file = build_receipt_pdf(payment)
    return send_file(pdf_file, as_attachment=True, download_name=f"receipt_{payment.id}.pdf", mimetype="application/pdf")
