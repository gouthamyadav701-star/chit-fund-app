from __future__ import annotations

import re
from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    FloatField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, EqualTo, NumberRange, Optional, Regexp, ValidationError


def strong_password(form, field):
    password = field.data or ""
    if len(password) < 8:
        raise ValidationError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", password):
        raise ValidationError("Password must include an uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValidationError("Password must include a lowercase letter.")
    if not re.search(r"\d", password):
        raise ValidationError("Password must include a number.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValidationError("Password must include a special character.")


class EmptyForm(FlaskForm):
    submit = SubmitField("Submit")


class RegisterForm(FlaskForm):
    account_type = SelectField(
        "Account Type",
        choices=[("Customer", "Customer"), ("Staff", "Staff / Office User")],
        validators=[DataRequired()],
        default="Customer",
    )
    business_name = StringField("Business Name", validators=[Optional()])
    business_code = StringField("Business Code", validators=[Optional()])
    username = StringField("Username", validators=[Optional()])
    email = StringField("Email", validators=[Optional(), Email()])
    phone = StringField(
        "Phone",
        validators=[Optional(), Regexp(r"^\+?\d{10,15}$", message="Use a valid phone number.")],
    )
    password = PasswordField("Password", validators=[DataRequired(), strong_password])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    role = SelectField(
        "Role",
        choices=[("Viewer", "Viewer"), ("Manager", "Manager"), ("Admin", "Admin")],
        validators=[Optional()],
    )
    submit = SubmitField("Register")

    def validate(self, extra_validators=None):
        is_valid = super().validate(extra_validators=extra_validators)
        if not is_valid:
            return False

        if self.account_type.data == "Customer":
            if not (self.phone.data or "").strip():
                self.phone.errors.append("Phone number is required for customer accounts.")
                return False
            if not (self.business_code.data or "").strip():
                self.business_code.errors.append("Business code is required for customer accounts.")
                return False
        else:
            if not (self.username.data or "").strip():
                self.username.errors.append("Username is required for staff accounts.")
                return False
            if not (self.email.data or "").strip():
                self.email.errors.append("Email is required for staff accounts.")
                return False
            if not self.role.data:
                self.role.errors.append("Role is required for staff accounts.")
                return False
            if not (self.business_name.data or "").strip() and not (self.business_code.data or "").strip():
                self.business_name.errors.append("Enter a new business name or an existing business code.")
                return False
        return True


class LoginForm(FlaskForm):
    business_code = StringField("Business Code", validators=[DataRequired()])
    username = StringField("Username or Phone", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


class RecoverBusinessCodeForm(FlaskForm):
    identifier = StringField("Username, Email, or Phone", validators=[DataRequired()])
    submit = SubmitField("Find Business Code")


class BusinessSettingsForm(FlaskForm):
    name = StringField("Business Name", validators=[DataRequired()])
    contact_phone = StringField(
        "Contact Phone",
        validators=[Optional(), Regexp(r"^\+?\d{10,15}$", message="Use a valid phone number.")],
    )
    contact_email = StringField("Contact Email", validators=[Optional(), Email()])
    receipt_header = StringField("Receipt Header", validators=[Optional()])
    logo_url = StringField("Logo Image URL", validators=[Optional()])
    submit = SubmitField("Save Settings")


class MemberForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    email = StringField("Email (Optional)", validators=[Optional(), Email()])
    phone = StringField(
        "Phone",
        validators=[
            DataRequired(message="Phone number is required."),
            Regexp(r"^\d{10}$", message="Use a 10-digit phone number."),
        ],
    )
    group_id = SelectField("Primary Chit Group", coerce=int, validators=[Optional()])
    submit = SubmitField("Save Member")


class MembershipForm(FlaskForm):
    group_id = SelectField("Register In Group", coerce=int, validators=[DataRequired()])
    member_number = StringField("Member Number", validators=[Optional()])
    share_units = SelectField(
        "Share Type",
        coerce=float,
        choices=[(1.0, "Full Chit"), (0.5, "Half Chit"), (0.25, "Quarter Chit")],
        validators=[DataRequired()],
        default=1.0,
    )
    submit = SubmitField("Add Chit Slot")


class PaymentForm(FlaskForm):
    membership_id = SelectField("Membership", coerce=int, validators=[DataRequired()])
    amount = FloatField("Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField("Record Payment")


class PaymentFilterForm(FlaskForm):
    member_id = SelectField("Member", coerce=int, validators=[Optional()])
    group_id = SelectField("Group", coerce=int, validators=[Optional()])
    status = SelectField(
        "Status",
        choices=[("", "All"), ("Paid", "Paid"), ("Partial", "Partial"), ("Pending", "Pending"), ("Overdue", "Overdue")],
        validators=[Optional()],
    )
    date_from = DateField("From", validators=[Optional()])
    date_to = DateField("To", validators=[Optional()])
    submit = SubmitField("Apply")


class ChitGroupForm(FlaskForm):
    name = StringField("Group Name", validators=[DataRequired()])
    monthly_amount = FloatField("Monthly Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    total_members = IntegerField("Total Members", validators=[DataRequired(), NumberRange(min=1)])
    start_date = DateField("Start Date", validators=[DataRequired()], default=date.today)
    auction_day = IntegerField("Auction Day Of Month", validators=[DataRequired(), NumberRange(min=1, max=28)], default=5)
    submit = SubmitField("Create Group")


class ExistingGroupSetupForm(FlaskForm):
    submit = SubmitField("Save Existing Group Setup")


class RoundForm(FlaskForm):
    next_round = HiddenField("Next Round", validators=[DataRequired()])
    submit = SubmitField("Advance Round")


class AuctionBidForm(FlaskForm):
    membership_id = SelectField("Winner", coerce=int, validators=[DataRequired()])
    bid_amount = FloatField("Payout Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    note = TextAreaField("Note", validators=[Optional()])
    submit = SubmitField("Save Winner")


class AuctionCloseForm(FlaskForm):
    submit = SubmitField("Close Auction")
