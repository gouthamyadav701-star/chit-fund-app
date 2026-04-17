from __future__ import annotations

import re
from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, FloatField, HiddenField, IntegerField, PasswordField, SelectField, StringField, SubmitField
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
    username = StringField("Username", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), strong_password])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    role = SelectField(
        "Role",
        choices=[("Viewer", "Viewer"), ("Manager", "Manager"), ("Admin", "Admin")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Register")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


class MemberForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    email = StringField("Email", validators=[Optional(), Email()])
    phone = StringField("Phone", validators=[Optional(), Regexp(r"^\d{10}$", message="Use a 10-digit phone number.")])
    total_amount = FloatField("Total Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    group_id = SelectField("Chit Group", coerce=int, validators=[Optional()])
    submit = SubmitField("Save Member")


class PaymentForm(FlaskForm):
    amount = FloatField("Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    submit = SubmitField("Record Payment")


class ChitGroupForm(FlaskForm):
    name = StringField("Group Name", validators=[DataRequired()])
    monthly_amount = FloatField("Monthly Amount", validators=[DataRequired(), NumberRange(min=0.01)])
    total_members = IntegerField("Total Members", validators=[DataRequired(), NumberRange(min=1)])
    start_date = DateField("Start Date", validators=[DataRequired()], default=date.today)
    submit = SubmitField("Create Group")


class RoundForm(FlaskForm):
    next_round = HiddenField("Next Round", validators=[DataRequired()])
    submit = SubmitField("Advance Round")
