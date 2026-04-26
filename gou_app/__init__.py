import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask
from sqlalchemy import inspect, text

from .blueprints.auth import auth_bp
from .blueprints.core import core_bp
from .blueprints.members import members_bp
from .blueprints.payments import payments_bp
from .config import Config
from .blueprints.api import api_bp
from .blueprints.auctions import auctions_bp
from .extensions import bcrypt, csrf, db, limiter, login_manager, mail, migrate


def configure_logging(app: Flask) -> None:
    os.makedirs(os.path.dirname(app.config["APP_LOG_FILE"]), exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(app.config["APP_LOG_FILE"], maxBytes=1_048_576, backupCount=5)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    if not any(isinstance(handler, RotatingFileHandler) for handler in app.logger.handlers):
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)


def ensure_runtime_schema(app: Flask) -> None:
    with app.app_context():
        db.create_all()

        inspector = inspect(db.engine)

        def columns_for(table_name: str) -> set[str]:
            if not inspector.has_table(table_name):
                return set()
            return {column["name"] for column in inspector.get_columns(table_name)}

        statements: list[str] = []

        chit_group_columns = columns_for("chit_group")
        if "auction_day" not in chit_group_columns:
            statements.append(
                "ALTER TABLE chit_group ADD COLUMN auction_day INTEGER NOT NULL DEFAULT 5"
            )

        membership_columns = columns_for("group_membership")
        if membership_columns:
            if "share_units" not in membership_columns:
                statements.append(
                    "ALTER TABLE group_membership ADD COLUMN share_units NUMERIC(4, 2) NOT NULL DEFAULT 1.00"
                )
            if "slot_number" not in membership_columns:
                statements.append(
                    "ALTER TABLE group_membership ADD COLUMN slot_number INTEGER NOT NULL DEFAULT 1"
                )

        payment_columns = columns_for("payment")
        if payment_columns:
            if "group_id" not in payment_columns:
                statements.append("ALTER TABLE payment ADD COLUMN group_id INTEGER")
            if "membership_id" not in payment_columns:
                statements.append("ALTER TABLE payment ADD COLUMN membership_id INTEGER")
            if "cycle_id" not in payment_columns:
                statements.append("ALTER TABLE payment ADD COLUMN cycle_id INTEGER")
            if "expected_amount" not in payment_columns:
                statements.append(
                    "ALTER TABLE payment ADD COLUMN expected_amount NUMERIC(10, 2) NOT NULL DEFAULT 0"
                )
            if "penalty_amount" not in payment_columns:
                statements.append(
                    "ALTER TABLE payment ADD COLUMN penalty_amount NUMERIC(10, 2) NOT NULL DEFAULT 0"
                )
            if "status" not in payment_columns:
                statements.append(
                    "ALTER TABLE payment ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'Paid'"
                )
            if "due_date" not in payment_columns:
                statements.append("ALTER TABLE payment ADD COLUMN due_date DATE")

        if statements:
            with db.engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))
            app.logger.info("Applied runtime schema compatibility updates: %s", statements)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(Config)

    configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    ensure_runtime_schema(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auctions_bp)
    app.register_blueprint(core_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(payments_bp)

    @app.errorhandler(403)
    def forbidden(_error):
        return "Forbidden", 403

    @app.errorhandler(404)
    def not_found(_error):
        return "Not found", 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled server error: %s", error)
        return "Internal server error", 500

    return app
