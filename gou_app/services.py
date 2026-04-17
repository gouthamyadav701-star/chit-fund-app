from __future__ import annotations

import logging
import os
import tempfile
from io import BytesIO
from threading import Thread

from dateutil.relativedelta import relativedelta
from flask import current_app
from flask_mail import Message
from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .extensions import mail
from .models import ChitGroup, InstallmentSchedule, Member, Payment


def generate_installment_schedule(group: ChitGroup, actor_id: int | None = None) -> None:
    group.schedules.clear()
    for round_number in range(1, group.total_members + 1):
        due_date = group.start_date + relativedelta(months=round_number - 1)
        schedule = InstallmentSchedule(
            round_number=round_number,
            due_date=due_date,
            expected_amount=group.monthly_amount,
            created_by=actor_id,
            updated_by=actor_id,
        )
        group.schedules.append(schedule)


def queue_payment_notifications(member: Member, payment: Payment) -> None:
    app = current_app._get_current_object()
    payload = {
        "member_name": member.name,
        "member_email": member.email,
        "member_phone": member.phone,
        "amount": float(payment.amount),
        "timestamp": payment.formatted_timestamp,
    }
    Thread(target=_send_notifications, args=(app, payload), daemon=True).start()


def _send_notifications(app, payload: dict) -> None:
    with app.app_context():
        _send_sms(app, payload)
        _send_email(payload)


def _send_sms(app, payload: dict) -> None:
    if not (payload["member_phone"] and app.config.get("TWILIO_ACCOUNT_SID") and app.config.get("TWILIO_AUTH_TOKEN") and app.config.get("TWILIO_PHONE_NUMBER")):
        return
    try:
        from twilio.rest import Client

        client = Client(app.config["TWILIO_ACCOUNT_SID"], app.config["TWILIO_AUTH_TOKEN"])
        client.messages.create(
            body=f"Payment received from {payload['member_name']}: Rs {payload['amount']:.2f} on {payload['timestamp']}.",
            from_=app.config["TWILIO_PHONE_NUMBER"],
            to=payload["member_phone"],
        )
    except Exception:
        app.logger.exception("SMS notification failed")


def _send_email(payload: dict) -> None:
    if not payload["member_email"]:
        return
    try:
        message = Message(
            subject="Payment received",
            recipients=[payload["member_email"]],
            body=(
                f"Hello {payload['member_name']},\n\n"
                f"We received your payment of Rs {payload['amount']:.2f} on {payload['timestamp']}.\n"
                "Thank you."
            ),
        )
        mail.send(message)
    except Exception:
        logging.getLogger(__name__).exception("Email notification failed")


def build_payment_excel(payments: list[Payment]) -> BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Payments"
    headers = ["Payment ID", "Member", "Group", "Amount", "Timestamp"]
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font

    for payment in payments:
        sheet.append(
            [
                payment.id,
                payment.member.name,
                payment.member.group.name if payment.member.group else "Unassigned",
                float(payment.amount),
                payment.formatted_timestamp,
            ]
        )

    for column in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column) + 2
        sheet.column_dimensions[column[0].column_letter].width = max_length

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def build_receipt_pdf(payment: Payment) -> BytesIO:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_file.close()

    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, "CHIT FUND RECEIPT", ln=1, align="C")
        pdf.ln(6)
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f"Member: {payment.member.name}", ln=1)
        pdf.cell(0, 10, f"Member ID: {payment.member.id}", ln=1)
        pdf.cell(0, 10, f"Amount Paid: Rs {float(payment.amount):.2f}", ln=1)
        pdf.cell(0, 10, f"Date: {payment.formatted_timestamp}", ln=1)
        pdf.cell(0, 10, "Thank you for your payment.", ln=1)
        pdf.output(temp_file.name)

        with open(temp_file.name, "rb") as pdf_handle:
            output = BytesIO(pdf_handle.read())
        output.seek(0)
        return output
    finally:
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
