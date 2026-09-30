"""Flask application factory."""
from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .config import load_secret_key, settings_from_env
from .i18n import init_i18n
from .security import init_security
from .services import storage


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(settings_from_env())
    app.config["DATABASE"] = os.path.join(app.instance_path, "app.db")
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key(app.instance_path)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    init_security(app)
    init_i18n(app)
    storage.init_db(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from .routes.core import bp as core_bp
    from .routes.screening import bp as screening_bp
    from .routes.sessions import bp as sessions_bp
    from .routes.vacancies import bp as vacancies_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(screening_bp)
    app.register_blueprint(vacancies_bp)


def _wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return request.is_json or best == "application/json"


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        code = exc.code or 500
        if _wants_json():
            return jsonify(error=exc.name, code=code), code
        return render_template("error.html", code=code), code

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        app.logger.exception("Unhandled error")
        if _wants_json():
            return jsonify(error="Internal Server Error", code=500), 500
        return render_template("error.html", code=500), 500
