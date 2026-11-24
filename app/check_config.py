"""`python -m app.check_config` — validate the configuration and print it (APP-01).

Works even when the configuration is broken (unlike `flask --app app ...`, which has to build
the app first). Secrets are shown only as "set" / "not set".
"""
from __future__ import annotations

from .config import ConfigError, load_settings


def main(argv: list[str] | None = None) -> int:
    """Check the configuration without starting the app; exit code 1 lists the problems."""
    from dotenv import load_dotenv

    load_dotenv()
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc)
        return 1
    rows = settings.report()
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name.ljust(width)}  {value}")
    print("Configuration OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
