from . import db
from flask_login import UserMixin
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
def now_ist():
    return datetime.now(IST)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    is_approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now_ist)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    chit_amount = db.Column(db.Float, nullable=False)
    deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now_ist)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'))
    amount = db.Column(db.Float)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)
    payment_date = db.Column(db.DateTime, default=now_ist)

    member = db.relationship('Member', backref='payments')

    __table_args__ = (
        db.Index('idx_member_date', 'member_id', 'payment_date'),
    )