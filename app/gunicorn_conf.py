"""APP-04: gunicorn settings for Linux containers (`gunicorn -c python:app.gunicorn_conf "app:create_app()"`).

Use it only with WORKER_MODE=external: analysis then runs in `flask --app app worker`
processes, so the web processes hold no queue and can be several. On Windows (the laptop)
the site keeps using waitress.
"""
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_PROCESSES", "2"))
threads = int(os.environ.get("WEB_THREADS", "4"))
worker_class = "gthread"
timeout = 60  # a request stuck longer than this is killed and the process restarted
graceful_timeout = int(os.environ.get("SHUTDOWN_TIMEOUT", "25"))  # APP-08: finish requests on SIGTERM
keepalive = 5
max_requests = 2000  # recycle processes now and then (memory)
max_requests_jitter = 200
accesslog = None  # requests are not logged: URLs may carry ids; errors still go to stderr
errorlog = "-"
forwarded_allow_ips = os.environ.get("TRUSTED_PROXIES", "127.0.0.1")
# gunicorn >= 25 opens a control socket in $HOME; the container's home is read-only and the
# app does not use it (no `gunicornc`), so it stays off.
control_socket_disable = True


# OBS-01: with PROMETHEUS_MULTIPROC_DIR every web process writes its metrics to that folder and
# /metrics adds them up; it must start empty and forget processes that exited.
def on_starting(server):  # noqa: ARG001 - gunicorn hook signature
    folder = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        for name in os.listdir(folder):
            if name.endswith(".db"):
                os.remove(os.path.join(folder, name))


def child_exit(server, worker):  # noqa: ARG001 - gunicorn hook signature
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
