from flask import Blueprint, jsonify
from flask_login import login_required

from ..models import ChitCycle, ChitGroup, GroupMembership
from ..services import build_dashboard_metrics

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/dashboard")
@login_required
def dashboard_summary():
    return jsonify(build_dashboard_metrics())


@api_bp.route("/defaulters")
@login_required
def defaulters():
    memberships = GroupMembership.query.filter_by(deleted=False, status="Active").all()
    payload = [
        {
            "membership_id": membership.id,
            "member": membership.member.name,
            "group": membership.group.name,
            "status": membership.payment_status,
            "outstanding_amount": membership.outstanding_amount,
            "penalty_balance": float(membership.penalty_balance),
        }
        for membership in memberships
        if membership.payment_status in {"Pending", "Overdue"}
    ]
    return jsonify(payload)


@api_bp.route("/groups/<int:group_id>/auctions")
@login_required
def group_auctions(group_id):
    cycles = ChitCycle.query.filter_by(group_id=group_id, deleted=False).order_by(ChitCycle.cycle_number.asc()).all()
    payload = [
        {
            "cycle_number": cycle.cycle_number,
            "status": cycle.status,
            "auction_date": cycle.auction_date.isoformat(),
            "winner": cycle.winner_membership.member.name if cycle.winner_membership else None,
            "winning_bid_amount": float(cycle.winning_bid_amount or 0),
            "discount_amount": float(cycle.discount_amount or 0),
            "dividend_per_member": float(cycle.dividend_per_member or 0),
        }
        for cycle in cycles
    ]
    return jsonify(payload)
