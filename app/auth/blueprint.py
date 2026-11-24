"""The `auth` blueprint (sign-in pages and admin pages); its routes live in login.py and admin.py."""
from flask import Blueprint

bp = Blueprint("auth", "app.auth")
