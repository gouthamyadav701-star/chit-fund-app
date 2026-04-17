from flask import Blueprint, current_app, flash, redirect, render_template, send_file, url_for
from flask_login import current_user, login_required

from ..decorators import manager_required
from ..extensions import db
from ..forms import PaymentForm
from ..models import Member, Payment
from ..services import build_payment_excel, build_receipt_pdf, queue_payment_notifications

payments_bp = Blueprint("payments", __name__)


@payments_bp.route("/payments/<int:member_id>/new", methods=["GET", "POST"])
@manager_required
def make_payment(member_id):
    member = Member.query.filter_by(id=member_id, deleted=False).first_or_404()
    form = PaymentForm()

    if form.validate_on_submit():
        amount = round(form.amount.data, 2)
        if amount > member.due_amount:
            flash(f"Overpayment not allowed. Due is Rs {member.due_amount:.2f}.", "danger")
            return render_template("payment.html", form=form, member=member)

        try:
            payment = Payment(
                member_id=member.id,
                amount=amount,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            member.paid_amount = round(float(member.paid_amount) + amount, 2)
            member.updated_by = current_user.id
            db.session.add(payment)
            db.session.commit()
            queue_payment_notifications(member, payment)
            flash(f"Payment of Rs {amount:.2f} added for {member.name}.", "success")
            return redirect(url_for("core.dashboard"))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Payment failed for member %s", member.id)
            flash("The payment could not be recorded.", "danger")

    return render_template("payment.html", form=form, member=member)


@payments_bp.route("/payments/history")
@login_required
def history():
    payments = (
        Payment.query.filter_by(deleted=False)
        .order_by(Payment.timestamp.desc())
        .all()
    )
    return render_template("history.html", payments=payments)


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
