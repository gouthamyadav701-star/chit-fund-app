from __future__ import annotations

import re

from flask_login import current_user

from .models import Business


def normalize_business_code(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned[:32]


def generate_business_code(name: str) -> str:
    base = normalize_business_code(name) or "business"
    code = base
    suffix = 2
    while Business.query.filter_by(code=code, deleted=False).first():
        code = f"{base}-{suffix}"
        suffix += 1
    return code


def current_business_id() -> int | None:
    if not getattr(current_user, "is_authenticated", False):
        return None
    return getattr(current_user, "business_id", None)


def current_business() -> Business | None:
    business_id = current_business_id()
    if not business_id:
        return None
    return Business.query.filter_by(id=business_id, deleted=False).first()
