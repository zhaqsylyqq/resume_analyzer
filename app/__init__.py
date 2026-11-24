"""Flask application factory."""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .auth import init_auth
from .cli import init_cli
from .config import load_data_keyring, load_secret_key, load_settings
from .i18n import init_i18n, translate
from .retention import init_retention
from .security import init_security
from .services import fieldcrypt, storage

# Commands that only read the database. Started for them, the app changes nothing: no schema
# migration, no encryption pass, no retention purge, no new key file. Incident 2026-11-24: the
# scheduled `backup-auto` ran newer code that was on the disk for a few minutes and migrated
# and encrypted the live database.
# `routes` only lists the URLs: looking at the app must not migrate or purge the database.
READ_ONLY_COMMANDS = frozenset({"backup", "backup-offsite", "backup-auto", "restore-check", "db-check", "routes"})
_FLASK_OPTIONS_WITH_VALUE = {"-A", "--app", "-e", "--env-file"}


def cli_command(argv: list[str] | None = None) -> str | None:
    """The `flask` sub-command this process runs (`flask --app app backup-auto`,
    `python -m flask --app app backup-auto`) or None (web server, tests, scripts)."""
    argv = sys.argv if argv is None else argv
    if not argv:
        return None
    parts = argv[0].replace("\\", "/").lower().split("/")  # Windows and POSIX paths alike
    if parts[-1] not in ("flask", "flask.exe") and parts[-2:] != ["flask", "__main__.py"]:
        return None
    args = iter(argv[1:])
    for arg in args:
        if arg in _FLASK_OPTIONS_WITH_VALUE:
            next(args, None)
        elif not arg.startswith("-"):
            return arg
    return None


def create_app(overrides: dict[str, Any] | None = None, read_only: bool | None = None) -> Flask:
    """Build the app. Raises ConfigError (listing every bad setting) before anything starts.

    read_only (default: the process runs one of READ_ONLY_COMMANDS): leave the database and the
    data folder exactly as they are."""
    if read_only is None:
        read_only = cli_command() in READ_ONLY_COMMANDS
    load_dotenv()
    settings = load_settings()
    app = Flask(__name__, instance_relative_config=True,
                instance_path=os.path.abspath(settings.data_dir) if settings.data_dir else None)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(settings.to_flask())
    # APP-02: PostgreSQL when DATABASE_URL is set, otherwise the SQLite file in the data folder
    app.config["DATABASE"] = settings.database_url or os.path.join(app.instance_path, "app.db")
    if settings.flask_secret_key:
        app.config["SECRET_KEY"] = settings.flask_secret_key
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key(app.instance_path, production=settings.is_production)
    # SEC-02b: personal data in the database is encrypted with this key (before any storage call)
    fieldcrypt.configure(load_data_keyring(app.config, app.instance_path, production=settings.is_production,
                                           read_only=read_only))
    app.extensions["settings"] = settings
    app.config["READ_ONLY_START"] = read_only

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # our Gemini service logs retries itself
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.WARNING)  # APP-02: migrations log only problems  # PDF export font subsetting is noisy

    init_security(app)
    init_i18n(app)
    if not read_only:
        storage.init_db(app)  # migrations, requeue, encryption pass (SEC-02b)
    init_auth(app)
    init_retention(app, run_now=not read_only)
    _register_blueprints(app)
    _register_error_handlers(app)
    init_cli(app)
    from .worker import init_worker

    init_worker(app)
    return app


def create_server_app() -> Flask:
    """Entry point for the web server (waitress `--call app:create_server_app`).

    Same as create_app(), plus QW-05: analysis interrupted by a restart continues on its own.
    CLI commands (`flask --app app ...`) keep using create_app() and never start workers.
    """
    from .services import jobs

    app = create_app()
    jobs.resume_pending(app)
    install_shutdown_handlers(app)
    return app


def install_shutdown_handlers(app: Flask) -> list[int]:
    """APP-08: on SIGTERM (docker stop, Kubernetes) or Ctrl+C, finish the resumes being analyzed
    (up to SHUTDOWN_TIMEOUT seconds), then stop. /readyz answers 503 from the first moment.

    Returns the signals handled. Signal handlers can only be set from the main thread; waitress
    calls the app factory there, other callers (tests, `flask run` reloader) are left alone.
    """
    import signal
    import threading

    from .services import jobs

    if threading.current_thread() is not threading.main_thread():
        return []
    timeout = app.config["SHUTDOWN_TIMEOUT"]
    log = logging.getLogger("app.shutdown")

    def handle(signum: int, _frame: Any) -> None:
        log.info("Received %s: finishing analysis in progress (up to %ss) before stopping",
                 signal.Signals(signum).name, timeout)
        jobs.drain(timeout)
        log.info("Stopped cleanly")
        raise SystemExit(0) if signum == signal.SIGTERM else KeyboardInterrupt

    handled = []
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):  # SIGBREAK: Ctrl+Break on Windows
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, handle)
            handled.append(int(sig))
    return handled


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
    known = (400, 403, 404, 413, 429)

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        code = exc.code or 500
        # keep the exception's own headers (Retry-After on 429, Allow on 405)
        headers = [(k, v) for k, v in exc.get_headers() if k.lower() != "content-type"]
        if _wants_json():
            body = jsonify(error=exc.name, code=code, message=translate(f"error.{code if code in known else 500}"))
            return body, code, headers
        return render_template("error.html", code=code), code, headers

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        app.logger.exception("Unhandled error")
        if _wants_json():
            return jsonify(error="Internal Server Error", code=500, message=translate("error.500")), 500
        return render_template("error.html", code=500), 500
