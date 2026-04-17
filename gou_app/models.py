from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from sqlalchemy import Index

from .extensions import db, login_manager

IST = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.utcnow()


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

    __table_args__ = (Index("ix_user_username", "username"),)


class ChitGroup(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    monthly_amount = db.Column(db.Numeric(10, 2), nullable=False)
    total_members = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    current_round = db.Column(db.Integer, nullable=False, default=1)
    members = db.relationship("Member", back_populates="group", lazy="select")
    schedules = db.relationship(
        "InstallmentSchedule",
        back_populates="group",
        lazy="select",
        cascade="all, delete-orphan",
        order_by="InstallmentSchedule.round_number",
    )

    @property
    def pool_value(self) -> float:
        return round(float(self.monthly_amount) * self.total_members, 2)

    @property
    def current_due_date(self):
        if 1 <= self.current_round <= len(self.schedules):
            return self.schedules[self.current_round - 1].due_date
        return None


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

    __table_args__ = (
        Index("ix_member_phone", "phone"),
        Index("ix_member_deleted", "deleted"),
    )

    @property
    def due_amount(self) -> float:
        return round(float(self.total_amount) - float(self.paid_amount), 2)

    @property
    def is_overdue(self) -> bool:
        if not self.group:
            return self.due_amount > 0
        expected = min(
            float(self.group.monthly_amount) * max(self.group.current_round, 1),
            float(self.total_amount),
        )
        return float(self.paid_amount) + 0.01 < expected


class Payment(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow)
    member = db.relationship("Member", back_populates="payments")

    __table_args__ = (
        Index("ix_payment_member_id", "member_id"),
        Index("ix_payment_timestamp", "timestamp"),
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


class InstallmentSchedule(db.Model, AuditMixin):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("chit_group.id"), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    expected_amount = db.Column(db.Numeric(10, 2), nullable=False)
    group = db.relationship("ChitGroup", back_populates="schedules")

    __table_args__ = (Index("ix_schedule_group_round", "group_id", "round_number", unique=True),)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return User.query.filter_by(id=int(user_id), deleted=False).first()
