"""Flask application factory."""
from __future__ import annotations

import logging
import os
from typing import Any

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .auth import init_auth
from .config import load_secret_key, load_settings
from .i18n import init_i18n, translate
from .retention import init_retention
from .security import init_security
from .services import storage


def create_app(overrides: dict[str, Any] | None = None) -> Flask:
    """Build the app. Raises ConfigError (listing every bad setting) before anything starts."""
    load_dotenv()
    settings = load_settings()
    app = Flask(__name__, instance_relative_config=True,
                instance_path=os.path.abspath(settings.data_dir) if settings.data_dir else None)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(settings.to_flask())
    app.config["DATABASE"] = os.path.join(app.instance_path, "app.db")
    if settings.flask_secret_key:
        app.config["SECRET_KEY"] = settings.flask_secret_key
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key(app.instance_path, production=settings.is_production)
    app.extensions["settings"] = settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # our Gemini service logs retries itself
    logging.getLogger("fontTools").setLevel(logging.WARNING)  # PDF export font subsetting is noisy

    init_security(app)
    init_i18n(app)
    storage.init_db(app)
    init_auth(app)
    init_retention(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    app.cli.add_command(config_check)
    return app


@click.command("config-check")
def config_check() -> None:
    """Validate the configuration and print it (secrets shown only as set / not set)."""
    from .check_config import main

    raise SystemExit(main())


def create_server_app() -> Flask:
    """Entry point for the web server (waitress `--call app:create_server_app`).

    Same as create_app(), plus QW-05: analysis interrupted by a restart continues on its own.
    CLI commands (`flask --app app ...`) keep using create_app() and never start workers.
    """
    from .services import jobs

    app = create_app()
    jobs.resume_pending(app)
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
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return request.is_json or best == "application/json"


def _register_error_handlers(app: Flask) -> None:
    known = (400, 403, 404, 413)

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        code = exc.code or 500
        if _wants_json():
            return jsonify(error=exc.name, code=code, message=translate(f"error.{code if code in known else 500}")), code
        return render_template("error.html", code=code), code

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        app.logger.exception("Unhandled error")
        if _wants_json():
            return jsonify(error="Internal Server Error", code=500, message=translate("error.500")), 500
        return render_template("error.html", code=500), 500
