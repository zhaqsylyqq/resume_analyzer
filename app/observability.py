"""OBS-01: request ids, logs without personal data, HTTP metrics, /metrics access, Sentry.

- Every request gets an id (the caller's `X-Request-ID` when it looks sane, otherwise a new
  one), returned in the `X-Request-ID` header and written on every log line of that request.
  The worker uses `resume-<id>` while it analyzes a resume.
- LOG_FORMAT=json writes one JSON object per line (for Loki / docker logs); text stays the
  default on the laptop. Both formats pass messages and tracebacks through `scrub()`: e-mail
  addresses, phone numbers and the input echoed by validation errors are replaced.
- /metrics is for the monitoring system only: it answers to requests from this machine or
  the private network that did not come through Cloudflare, or to `Authorization: Bearer
  <METRICS_TOKEN>`. Everyone else gets 404, like any page that does not exist.
- SENTRY_DSN (optional) sends unexpected errors to Sentry with no personal data: no cookies,
  headers, form data, query strings, local variables or user; messages scrubbed as above.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any

from flask import Flask, Response, abort, current_app, g, request

from . import metrics

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_SANE_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

# Headers a request carries when it came through Cloudflare (the public domain) or any proxy.
PROXY_HEADERS = ("CF-Connecting-IP", "CF-Ray", "CF-IPCountry", "X-Forwarded-For", "Forwarded", "X-Real-IP")

# Where a scraper may sit: the machine itself, or a private (RFC 1918 / ULA) network.
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w+])\+?\d(?:[ ()\-]{0,2}\d){9,14}(?![\w])")  # 10+ digits: not dates or ids
_ECHO = re.compile(r"(input_value=)(.*?)(, input_type=|$)", re.MULTILINE)


def scrub(text: str) -> str:
    """Replace what may identify a candidate: e-mails, phone numbers, echoed input."""
    text = _ECHO.sub(r"\1[redacted]\3", text)
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)


def request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> Any:
    """Bind a request id to this thread/context; returns the token for reset_request_id."""
    return _request_id.set(value)


def reset_request_id(token: Any) -> None:
    _request_id.reset(token)


# Logs ------------------------------------------------------------------------------------------

_base_factory = logging.getLogRecordFactory()


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _base_factory(*args, **kwargs)
    record.request_id = _request_id.get()
    return record


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return scrub(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": scrub(record.getMessage()),
        }
        if record.exc_info:
            entry["exception"] = scrub(self.formatException(record.exc_info))
        event = getattr(record, "event", None)  # SEC-03 audit events already carry structured data
        if isinstance(event, dict):
            entry["event"] = event
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(fmt: str = "text") -> None:
    logging.setLogRecordFactory(_record_factory)
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else TextFormatter()
    for handler in root.handlers:
        if type(handler) is logging.StreamHandler or isinstance(handler, logging.FileHandler):
            handler.setFormatter(formatter)


# Requests --------------------------------------------------------------------------------------

def _start_request() -> None:
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming if _SANE_ID.fullmatch(incoming) else uuid.uuid4().hex
    g._request_id_token = set_request_id(rid)
    g._started = time.perf_counter()


def _finish_request(response: Response) -> Response:
    response.headers["X-Request-ID"] = request_id()
    endpoint = request.endpoint or "none"  # unknown URLs share one label
    metrics.HTTP_REQUESTS.labels(request.method, endpoint, str(response.status_code)).inc()
    started = g.get("_started")
    if started is not None:
        metrics.HTTP_DURATION.labels(endpoint).observe(time.perf_counter() - started)
    return response


def _end_request(_exc: BaseException | None) -> None:
    token = g.pop("_request_id_token", None)
    if token is not None:
        try:
            reset_request_id(token)
        except ValueError:  # a different context (streamed response): nothing to restore
            pass


def is_internal_request() -> bool:
    """True for a scrape from this machine or the private network, never through Cloudflare."""
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    try:
        peer = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    return peer.is_loopback or any(peer in net for net in _PRIVATE_NETWORKS)


def metrics_allowed() -> bool:
    token = current_app.config.get("METRICS_TOKEN", "")
    given = request.headers.get("Authorization", "")
    if token and given.startswith("Bearer ") and hmac.compare_digest(given[7:].encode(), token.encode()):
        return True
    return is_internal_request()


def metrics_view() -> Response:
    if not metrics_allowed():
        abort(404)
    body = metrics.exposition(str(current_app.config["DATABASE"]))
    return Response(body, mimetype=None, content_type=metrics.CONTENT_TYPE)


# Sentry ----------------------------------------------------------------------------------------

def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    return value


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry before_send: keep what helps to fix the bug, drop what identifies a person."""
    req = event.get("request")
    if isinstance(req, dict):
        event["request"] = {k: req[k] for k in ("method", "url") if k in req}
        if "url" in event["request"]:
            event["request"]["url"] = str(event["request"]["url"]).split("?", 1)[0]
    for key in ("user", "extra", "server_name"):
        event.pop(key, None)
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = scrub(exc["value"])
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    if "logentry" in event:
        event["logentry"] = _scrub_value(event["logentry"])
    if isinstance(event.get("message"), str):
        event["message"] = scrub(event["message"])
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, dict):
        crumbs["values"] = [_scrub_value(c) for c in crumbs.get("values") or []]
    tags = event.setdefault("tags", {})
    if isinstance(tags, dict):
        tags["request_id"] = request_id()
    return event


def init_sentry(app: Flask, transport: Any = None) -> bool:
    dsn = app.config.get("SENTRY_DSN", "")
    if not dsn:
        return False
    import sentry_sdk

    options: dict[str, Any] = {
        "dsn": dsn,
        "environment": app.config.get("APP_ENV", "development"),
        # no send_default_pii (deprecated; off by default): Sentry's own scrubber stays on,
        # and scrub_event below removes the rest
        "include_local_variables": False,
        "max_request_body_size": "never",
        "traces_sample_rate": 0.0,
        "before_send": scrub_event,
    }
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(**options)
    return True


def init_observability(app: Flask) -> None:
    app.before_request(_start_request)
    app.after_request(_finish_request)
    app.teardown_request(_end_request)
    app.add_url_rule("/metrics", "metrics", metrics_view)
    init_sentry(app)
