import logging
import os
from datetime import UTC, datetime, timedelta
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
from .services import process_group_retention
from .tenant import current_business


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

        business_columns = columns_for("business")
        if business_columns:
            if "contact_phone" not in business_columns:
                statements.append("ALTER TABLE business ADD COLUMN contact_phone VARCHAR(30)")
            if "contact_email" not in business_columns:
                statements.append("ALTER TABLE business ADD COLUMN contact_email VARCHAR(255)")
            if "receipt_header" not in business_columns:
                statements.append("ALTER TABLE business ADD COLUMN receipt_header VARCHAR(255)")
            if "logo_url" not in business_columns:
                statements.append("ALTER TABLE business ADD COLUMN logo_url VARCHAR(500)")

        chit_group_columns = columns_for("chit_group")
        if "business_id" not in chit_group_columns:
            statements.append("ALTER TABLE chit_group ADD COLUMN business_id INTEGER")
        if "auction_day" not in chit_group_columns:
            statements.append(
                "ALTER TABLE chit_group ADD COLUMN auction_day INTEGER NOT NULL DEFAULT 5"
            )
        if "completed_on" not in chit_group_columns:
            statements.append("ALTER TABLE chit_group ADD COLUMN completed_on DATE")
        if "retention_expires_on" not in chit_group_columns:
            statements.append("ALTER TABLE chit_group ADD COLUMN retention_expires_on DATE")
        if "archive_export_sent_on" not in chit_group_columns:
            statements.append("ALTER TABLE chit_group ADD COLUMN archive_export_sent_on TIMESTAMP")
        if "archived_on" not in chit_group_columns:
            statements.append("ALTER TABLE chit_group ADD COLUMN archived_on TIMESTAMP")

        membership_columns = columns_for("group_membership")
        if membership_columns:
            if "business_id" not in membership_columns:
                statements.append("ALTER TABLE group_membership ADD COLUMN business_id INTEGER")
            if "share_units" not in membership_columns:
                statements.append(
                    "ALTER TABLE group_membership ADD COLUMN share_units NUMERIC(4, 2) NOT NULL DEFAULT 1.00"
                )
            if "slot_number" not in membership_columns:
                statements.append(
                    "ALTER TABLE group_membership ADD COLUMN slot_number INTEGER NOT NULL DEFAULT 1"
                )
            if "arrears_balance" not in membership_columns:
                statements.append(
                    "ALTER TABLE group_membership ADD COLUMN arrears_balance NUMERIC(10, 2) NOT NULL DEFAULT 0"
                )

        member_columns = columns_for("member")
        if member_columns and "business_id" not in member_columns:
            statements.append("ALTER TABLE member ADD COLUMN business_id INTEGER")

        cycle_columns = columns_for("chit_cycle")
        if cycle_columns and "business_id" not in cycle_columns:
            statements.append("ALTER TABLE chit_cycle ADD COLUMN business_id INTEGER")

        bid_columns = columns_for("auction_bid")
        if bid_columns and "business_id" not in bid_columns:
            statements.append("ALTER TABLE auction_bid ADD COLUMN business_id INTEGER")

        payment_columns = columns_for("payment")
        if payment_columns:
            if "business_id" not in payment_columns:
                statements.append("ALTER TABLE payment ADD COLUMN business_id INTEGER")
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

        schedule_columns = columns_for("installment_schedule")
        if schedule_columns and "business_id" not in schedule_columns:
            statements.append("ALTER TABLE installment_schedule ADD COLUMN business_id INTEGER")

        ledger_columns = columns_for("ledger_entry")
        if ledger_columns and "business_id" not in ledger_columns:
            statements.append("ALTER TABLE ledger_entry ADD COLUMN business_id INTEGER")

        audit_columns = columns_for("audit_log")
        if audit_columns and "business_id" not in audit_columns:
            statements.append("ALTER TABLE audit_log ADD COLUMN business_id INTEGER")

        user_columns = columns_for("user")
        if user_columns:
            if "member_id" not in user_columns:
                statements.append("ALTER TABLE \"user\" ADD COLUMN member_id INTEGER")
            if "business_id" not in user_columns:
                statements.append("ALTER TABLE \"user\" ADD COLUMN business_id INTEGER")

        if statements:
            with db.engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))
            app.logger.info("Applied runtime schema compatibility updates: %s", statements)
            inspector = inspect(db.engine)

        with db.engine.begin() as connection:
            default_business_id = connection.execute(text("SELECT id FROM business ORDER BY id ASC LIMIT 1")).scalar()
            if default_business_id is None:
                now = datetime.utcnow()
                connection.execute(
                    text(
                        "INSERT INTO business (name, code, created_at, updated_at, deleted) "
                        "VALUES (:name, :code, :created_at, :updated_at, :deleted)"
                    ),
                    {
                        "name": "Default Chit Business",
                        "code": "default",
                        "created_at": now,
                        "updated_at": now,
                        "deleted": False,
                    },
                )
                default_business_id = connection.execute(text("SELECT id FROM business ORDER BY id ASC LIMIT 1")).scalar()

            if default_business_id is not None:
                for table_name in [
                    "user",
                    "member",
                    "chit_group",
                    "group_membership",
                    "chit_cycle",
                    "auction_bid",
                    "payment",
                    "installment_schedule",
                    "ledger_entry",
                    "audit_log",
                ]:
                    table_columns = columns_for(table_name)
                    if "business_id" in table_columns:
                        quoted_table = f'"{table_name}"' if table_name == "user" else table_name
                        connection.execute(
                            text(f"UPDATE {quoted_table} SET business_id = :business_id WHERE business_id IS NULL"),
                            {"business_id": default_business_id},
                        )


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

    @app.context_processor
    def inject_business_context():
        return {"active_business": current_business()}

    app.extensions["group_retention_last_run_at"] = None

    @app.before_request
    def run_group_retention_cycle():
        last_run = app.extensions.get("group_retention_last_run_at")
        now = datetime.now(UTC)
        if last_run and now - last_run < timedelta(hours=12):
            return None
        try:
            process_group_retention()
            app.extensions["group_retention_last_run_at"] = now
        except Exception:
            db.session.rollback()
            app.logger.exception("Automatic group retention cleanup failed")
        return None

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
