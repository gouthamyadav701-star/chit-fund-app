from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..decorators import manager_required
from ..extensions import db
from ..forms import AuctionBidForm, AuctionCloseForm
from ..models import AuctionBid, ChitCycle, GroupMembership
from ..services import assign_cycle_winner, close_auction, log_audit

auctions_bp = Blueprint("auctions", __name__)


@auctions_bp.route("/auctions")
@login_required
def index():
    if current_user.role == "Customer":
        abort(403)
    cycles = ChitCycle.query.filter_by(deleted=False).order_by(ChitCycle.auction_date.asc()).all()
    bid_form = AuctionBidForm()
    close_form = AuctionCloseForm()
    return render_template("auctions.html", cycles=cycles, bid_form=bid_form, close_form=close_form)


@auctions_bp.route("/auctions/<int:cycle_id>/bid", methods=["POST"])
@manager_required
def place_bid(cycle_id):
    cycle = ChitCycle.query.filter_by(id=cycle_id, deleted=False).first_or_404()
    form = AuctionBidForm()
    memberships = [
        membership
        for membership in cycle.group.memberships
        if membership.status == "Active" and not membership.deleted and not membership.has_won_auction
    ]
    form.membership_id.choices = [(membership.id, f"{membership.member.name} - Slot {membership.slot_number} ({membership.share_label})") for membership in memberships]

    if form.validate_on_submit():
        membership = GroupMembership.query.filter_by(id=form.membership_id.data, deleted=False).first_or_404()
        if cycle.status == "Closed":
            flash("Winner already selected for this cycle.", "warning")
            return redirect(url_for("auctions.index"))
        if membership.group_id != cycle.group_id or membership.has_won_auction:
            flash("This member slot is not eligible for this month.", "danger")
            return redirect(url_for("auctions.index"))
        bid = assign_cycle_winner(
            cycle,
            membership,
            form.bid_amount.data,
            current_user.id,
            note=(form.note.data or "").strip() or None,
        )
        db.session.commit()
        flash(
            f"Winner saved for cycle {cycle.cycle_number}: {membership.member.name} - Slot {membership.slot_number}.",
            "success",
        )
    else:
        flash("Winner details could not be saved.", "danger")
    return redirect(url_for("auctions.index"))


@auctions_bp.route("/auctions/<int:cycle_id>/close", methods=["POST"])
@manager_required
def finalize(cycle_id):
    cycle = ChitCycle.query.filter_by(id=cycle_id, deleted=False).first_or_404()
    form = AuctionCloseForm()
    if form.validate_on_submit():
        winning_bid = close_auction(cycle, current_user.id)
        if not winning_bid:
            flash("No bids available to close this auction.", "warning")
        else:
            db.session.commit()
            flash(f"Auction closed. Winner: {winning_bid.membership.member.name}.", "success")
    return redirect(url_for("auctions.index"))
