"""Command-line tools: create-admin, reset-2fa, reset-link (A1, SEC-01)."""
from __future__ import annotations

import click
from flask import current_app
from flask.cli import with_appcontext

from ..services import storage
from ..starter import create_starter_session
from .login import create_reset_link
from .passwords import MIN_PASSWORD, hash_password, validate_password, validate_username


@click.command("create-admin")
@with_appcontext
@click.option("--username", prompt=True, help="Login name of the new admin.")
@click.password_option("--password", help="Asked interactively (hidden, with confirmation) if omitted.")
def create_admin(username: str, password: str) -> None:
    """Create an admin user (there is no default admin or password)."""
    username = username.strip()
    db = storage.connect(current_app.config["DATABASE"])
    try:
        if validate_username(username):
            raise click.ClickException("Username: 3-64 letters, digits, '.', '_' or '-'.")
        if validate_password(password):
            raise click.ClickException(f"Password must be at least {MIN_PASSWORD} characters.")
        if storage.get_user_by_name(db, username):
            raise click.ClickException(f"User '{username}' already exists.")
        user_id = storage.create_user(db, username, hash_password(password), "admin")
        create_starter_session(db, user_id, current_app.config["STARTER_SESSION"])
        storage.audit(db, "user_create", username="cli", detail=f"{username} (admin) id={user_id}")
    finally:
        db.close()
    click.echo(f"Admin '{username}' created.")


@click.command("reset-2fa")
@with_appcontext
@click.argument("username")
def reset_2fa(username: str) -> None:
    """Switch off two-factor sign-in for a user who lost the phone and the recovery codes."""
    db = storage.connect(current_app.config["DATABASE"])
    try:
        user = storage.get_user_by_name(db, username.strip())
        if user is None:
            raise click.ClickException(f"User '{username}' not found.")
        storage.update_user(db, user["id"], totp_secret=None, totp_last_step=None, recovery_codes=None)
        storage.audit(db, "mfa_reset", username="cli", detail=f"{user['username']}: switched off from the command line")
    finally:
        db.close()
    click.echo(f"Two-factor sign-in switched off for '{user['username']}'. They should switch it on again.")


@click.command("reset-link")
@with_appcontext
@click.argument("username")
@click.option("--base-url", default="https://hrscreen.date", show_default=True, help="Address the user opens.")
def reset_link_command(username: str, base_url: str) -> None:
    """Print a one-time password reset link (valid 1 hour), e.g. for the only admin."""
    db = storage.connect(current_app.config["DATABASE"])
    try:
        user = storage.get_user_by_name(db, username.strip())
        if user is None:
            raise click.ClickException(f"User '{username}' not found.")
        with current_app.test_request_context(base_url=base_url):
            link = create_reset_link(db, user)
        storage.audit(db, "password_reset_link", username="cli", detail=f"{user['username']}: link created")
    finally:
        db.close()
    click.echo(link)
