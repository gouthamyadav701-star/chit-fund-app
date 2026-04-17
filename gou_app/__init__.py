import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask

from .blueprints.auth import auth_bp
from .blueprints.core import core_bp
from .blueprints.members import members_bp
from .blueprints.payments import payments_bp
from .config import Config
from .extensions import csrf, db, limiter, login_manager, mail, migrate


def configure_logging(app: Flask) -> None:
    os.makedirs(os.path.dirname(app.config["APP_LOG_FILE"]), exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = RotatingFileHandler(app.config["APP_LOG_FILE"], maxBytes=1_048_576, backupCount=5)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    if not any(isinstance(handler, RotatingFileHandler) for handler in app.logger.handlers):
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(Config)

    configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    app.register_blueprint(auth_bp)
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
