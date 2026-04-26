from functools import wraps

from flask import abort
from flask_login import current_user


def manager_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in {"Admin", "Manager"}:
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "Admin":
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped
