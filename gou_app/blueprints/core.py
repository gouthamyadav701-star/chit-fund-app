from sqlalchemy import text
from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required

from ..extensions import db
from ..forms import EmptyForm, RoundForm
from ..models import ChitGroup, Member, User

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
@login_required
def dashboard():
    members = Member.query.filter_by(deleted=False).order_by(Member.name).all()
    groups = ChitGroup.query.filter_by(deleted=False).order_by(ChitGroup.name).all()
    pending_users = []
    if current_user.role == "Admin":
        pending_users = User.query.filter_by(is_approved=False, deleted=False).order_by(User.created_at.asc()).all()

    total_paid = round(sum(float(member.paid_amount) for member in members), 2)
    total_due = round(sum(member.due_amount for member in members), 2)
    action_form = EmptyForm()
    round_forms = {group.id: RoundForm(next_round=str(min(group.current_round + 1, group.total_members))) for group in groups}

    return render_template(
        "dashboard.html",
        members=members,
        groups=groups,
        pending_users=pending_users,
        total_paid=total_paid,
        total_due=total_due,
        action_form=action_form,
        round_forms=round_forms,
    )


@core_bp.route("/health")
def health():
    db.session.execute(text("SELECT 1"))
    return jsonify({"status": "ok", "db": "connected"})
