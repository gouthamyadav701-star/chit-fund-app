from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from sqlalchemy import Index, UniqueConstraint

from .extensions import db, login_manager

IST = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.utcnow()


def today_ist() -> date:
    return datetime.now(IST).date()


class AuditMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    created_by = db.Column(db.Integer, nullable=True)
    updated_by = db.Column(db.Integer, nullable=True)
    deleted = db.Column(db.Boolean, nullable=False, default=False)


class User(UserMixin, db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="Viewer")
    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    audit_logs = db.relationship("AuditLog", back_populates="actor", lazy="select")

    __table_args__ = (Index("ix_user_username", "username"),)


class ChitGroup(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    monthly_amount = db.Column(db.Numeric(10, 2), nullable=False)
    total_members = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    current_round = db.Column(db.Integer, nullable=False, default=1)
    auction_day = db.Column(db.Integer, nullable=False, default=5)
    completed_on = db.Column(db.Date, nullable=True)
    retention_expires_on = db.Column(db.Date, nullable=True)
    archive_export_sent_on = db.Column(db.DateTime, nullable=True)
    archived_on = db.Column(db.DateTime, nullable=True)

    members = db.relationship("Member", back_populates="group", lazy="select")
    schedules = db.relationship(
        "InstallmentSchedule",
        back_populates="group",
        lazy="select",
        cascade="all, delete-orphan",
        order_by="InstallmentSchedule.round_number",
    )
    memberships = db.relationship(
        "GroupMembership",
        back_populates="group",
        lazy="select",
        cascade="all, delete-orphan",
    )
    cycles = db.relationship(
        "ChitCycle",
        back_populates="group",
        lazy="select",
        cascade="all, delete-orphan",
        order_by="ChitCycle.cycle_number",
    )
    payments = db.relationship("Payment", back_populates="group", lazy="select")
    ledger_entries = db.relationship("LedgerEntry", back_populates="group", lazy="select")

    @property
    def pool_value(self) -> float:
        return round(float(self.monthly_amount) * self.total_members, 2)

    @property
    def current_due_date(self):
        if 1 <= self.current_round <= len(self.schedules):
            return self.schedules[self.current_round - 1].due_date
        return None

    @property
    def next_auction_date(self):
        cycle = next((cycle for cycle in self.cycles if cycle.cycle_number == self.current_round), None)
        return cycle.auction_date if cycle else None

    @property
    def active_membership_count(self) -> int:
        return sum(1 for membership in self.memberships if membership.status == "Active" and not membership.deleted)

    @property
    def is_completed(self) -> bool:
        return self.completed_on is not None

    @property
    def is_archived(self) -> bool:
        return self.archived_on is not None or self.deleted


class Member(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    paid_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=True)

    group = db.relationship("ChitGroup", back_populates="members")
    payments = db.relationship("Payment", back_populates="member", lazy="select", cascade="all, delete-orphan")
    memberships = db.relationship(
        "GroupMembership",
        back_populates="member",
        lazy="select",
        cascade="all, delete-orphan",
    )
    ledger_entries = db.relationship("LedgerEntry", back_populates="member", lazy="select")

    __table_args__ = (
        Index("ix_member_phone", "phone"),
        Index("ix_member_deleted", "deleted"),
    )

    @property
    def active_memberships(self) -> list["GroupMembership"]:
        return [membership for membership in self.memberships if membership.status == "Active" and not membership.deleted]

    @property
    def active_group_names(self) -> str:
        groups = [membership.group.name for membership in self.active_memberships if membership.group]
        return ", ".join(sorted(set(groups))) or "Unassigned"

    @property
    def due_amount(self) -> float:
        return round(float(self.total_amount) - float(self.paid_amount), 2)

    @property
    def is_overdue(self) -> bool:
        return any(membership.is_overdue for membership in self.active_memberships) or self.due_amount > 0


class GroupMembership(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=False)
    joined_on = db.Column(db.Date, nullable=False, default=today_ist)
    status = db.Column(db.String(20), nullable=False, default="Active")
    member_number = db.Column(db.String(30), nullable=True)
    slot_number = db.Column(db.Integer, nullable=False, default=1)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    share_units = db.Column(db.Numeric(4, 2), nullable=False, default=Decimal("1.00"))
    total_dividend = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    penalty_balance = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))

    member = db.relationship("Member", back_populates="memberships")
    group = db.relationship("ChitGroup", back_populates="memberships")
    payments = db.relationship("Payment", back_populates="membership", lazy="select")
    bids = db.relationship("AuctionBid", back_populates="membership", lazy="select")
    cycles_won = db.relationship("ChitCycle", back_populates="winner_membership", lazy="select")
    ledger_entries = db.relationship("LedgerEntry", back_populates="membership", lazy="select")

    __table_args__ = (
        Index("ix_membership_group_status", "group_id", "status"),
        Index("ix_membership_member_group_slot", "member_id", "group_id", "slot_number"),
    )

    @property
    def current_cycle(self) -> "ChitCycle | None":
        if not self.group:
            return None
        return next((cycle for cycle in self.group.cycles if cycle.cycle_number == self.group.current_round), None)

    @property
    def current_cycle_paid_amount(self) -> float:
        cycle = self.current_cycle
        if not cycle:
            return 0.0
        total = sum(float(payment.amount) for payment in self.payments if payment.cycle_id == cycle.id and not payment.deleted)
        return round(total, 2)

    @property
    def expected_amount(self) -> float:
        if not self.group:
            return 0.0
        return round(float(self.group.monthly_amount) * float(self.share_units), 2)

    @property
    def eligible_prize_amount(self) -> float:
        if not self.group:
            return 0.0
        return round(float(self.group.pool_value) * float(self.share_units), 2)

    @property
    def share_label(self) -> str:
        units = float(self.share_units)
        if units == 1.0:
            return "Full Chit"
        if units == 0.5:
            return "Half Chit"
        if units == 0.25:
            return "Quarter Chit"
        return f"{units:.2f} Share"

    @property
    def display_label(self) -> str:
        group_name = self.group.name if self.group else "Group"
        return f"{group_name} - Slot {self.slot_number} ({self.share_label})"

    @property
    def outstanding_amount(self) -> float:
        outstanding = max(self.expected_amount - self.current_cycle_paid_amount, 0)
        return round(outstanding, 2)

    @property
    def is_overdue(self) -> bool:
        cycle = self.current_cycle
        if not cycle or not cycle.due_date:
            return False
        return cycle.due_date < today_ist() and self.outstanding_amount > 0

    @property
    def payment_status(self) -> str:
        if self.outstanding_amount <= 0:
            return "Paid"
        if self.is_overdue:
            return "Overdue"
        return "Pending"

    @property
    def has_won_auction(self) -> bool:
        return any(cycle.status == "Closed" and not cycle.deleted for cycle in self.cycles_won)


class ChitCycle(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=False)
    cycle_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    auction_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Scheduled")
    expected_collection = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    collected_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    discount_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    dividend_per_member = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    penalty_total = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    winning_bid_amount = db.Column(db.Numeric(10, 2), nullable=True)
    winner_membership_id = db.Column(db.Integer, db.ForeignKey("group_membership.id"), nullable=True)

    group = db.relationship("ChitGroup", back_populates="cycles")
    bids = db.relationship("AuctionBid", back_populates="cycle", lazy="select", cascade="all, delete-orphan")
    payments = db.relationship("Payment", back_populates="cycle", lazy="select")
    winner_membership = db.relationship("GroupMembership", back_populates="cycles_won")
    ledger_entries = db.relationship("LedgerEntry", back_populates="cycle", lazy="select")

    __table_args__ = (
        UniqueConstraint("group_id", "cycle_number", name="uq_cycle_group_cycle_number"),
        Index("ix_cycle_group_status", "group_id", "status"),
    )

    @property
    def has_closed_auction(self) -> bool:
        return self.status == "Closed" and self.winner_membership_id is not None

    @property
    def winner_payout_amount(self) -> float:
        if not self.winner_membership:
            return 0.0
        return round(float(self.winning_bid_amount or 0) * float(self.winner_membership.share_units), 2)


class AuctionBid(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(db.Integer, db.ForeignKey("chit_cycle.id"), nullable=False)
    membership_id = db.Column(db.Integer, db.ForeignKey("group_membership.id"), nullable=False)
    bid_amount = db.Column(db.Numeric(10, 2), nullable=False)
    discount_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    is_winner = db.Column(db.Boolean, nullable=False, default=False)
    note = db.Column(db.String(255), nullable=True)

    cycle = db.relationship("ChitCycle", back_populates="bids")
    membership = db.relationship("GroupMembership", back_populates="bids")
    ledger_entries = db.relationship("LedgerEntry", back_populates="auction_bid", lazy="select")

    __table_args__ = (
        UniqueConstraint("cycle_id", "membership_id", name="uq_bid_cycle_membership"),
        Index("ix_bid_cycle_amount", "cycle_id", "bid_amount"),
    )


class Payment(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=True)
    membership_id = db.Column(db.Integer, db.ForeignKey("group_membership.id"), nullable=True)
    cycle_id = db.Column(db.Integer, db.ForeignKey("chit_cycle.id"), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    expected_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    penalty_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    status = db.Column(db.String(20), nullable=False, default="Paid")
    due_date = db.Column(db.Date, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow)

    member = db.relationship("Member", back_populates="payments")
    group = db.relationship("ChitGroup", back_populates="payments")
    membership = db.relationship("GroupMembership", back_populates="payments")
    cycle = db.relationship("ChitCycle", back_populates="payments")
    ledger_entries = db.relationship("LedgerEntry", back_populates="payment", lazy="select")

    __table_args__ = (
        Index("ix_payment_member_id", "member_id"),
        Index("ix_payment_timestamp", "timestamp"),
        Index("ix_payment_group_status", "group_id", "status"),
    )

    @property
    def local_timestamp(self) -> datetime | None:
        if self.timestamp is None:
            return None
        value = self.timestamp
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(IST)

    @property
    def formatted_timestamp(self) -> str:
        local_value = self.local_timestamp
        if local_value is None:
            return "-"
        return local_value.strftime("%d-%m-%Y %I:%M %p")

    @property
    def total_with_penalty(self) -> float:
        return round(float(self.amount) + float(self.penalty_amount), 2)


class InstallmentSchedule(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    expected_amount = db.Column(db.Numeric(10, 2), nullable=False)
    group = db.relationship("ChitGroup", back_populates="schedules")

    __table_args__ = (Index("ix_schedule_group_round", "group_id", "round_number", unique=True),)


class LedgerEntry(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=True)
    membership_id = db.Column(db.Integer, db.ForeignKey("group_membership.id"), nullable=True)
    cycle_id = db.Column(db.Integer, db.ForeignKey("chit_cycle.id"), nullable=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=True)
    auction_bid_id = db.Column(db.Integer, db.ForeignKey("auction_bid.id"), nullable=True)
    entry_type = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.String(255), nullable=False)

    group = db.relationship("ChitGroup", back_populates="ledger_entries")
    member = db.relationship("Member", back_populates="ledger_entries")
    membership = db.relationship("GroupMembership", back_populates="ledger_entries")
    cycle = db.relationship("ChitCycle", back_populates="ledger_entries")
    payment = db.relationship("Payment", back_populates="ledger_entries")
    auction_bid = db.relationship("AuctionBid", back_populates="ledger_entries")

    __table_args__ = (
        Index("ix_ledger_group_type", "group_id", "entry_type"),
        Index("ix_ledger_cycle", "cycle_id"),
    )


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    actor = db.relationship("User", back_populates="audit_logs")

    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return User.query.filter_by(id=int(user_id), deleted=False).first()
