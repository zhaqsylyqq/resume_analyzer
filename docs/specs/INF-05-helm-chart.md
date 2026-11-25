# INF-05 — Helm chart of the application

## What

`deploy/chart` (chart `hrscreen` 0.1.0) installs the whole app into one namespace:

| Object | Purpose |
|---|---|
| Deployment `-web` | gunicorn (`WEB_PROCESSES` × `WEB_THREADS`), `WORKER_MODE=external`; startup/liveness `/healthz`, readiness `/readyz` (503 while the database is down or the pod stops, APP-08); `maxUnavailable: 0`, `maxSurge: 1`, `preStop` 10 s so the Service drops the pod before gunicorn stops (5 s lost 1 request in 806 in a kind run); metrics of all gunicorn processes via `PROMETHEUS_MULTIPROC_DIR` (OBS-01) |
| Deployment `-worker` | `flask --app app worker --threads N --metrics-port 9101`; probes on the metrics port; finishes resumes in hand on SIGTERM (grace 35 s) |
| Job `-migrate` | Helm hook pre-install/pre-upgrade: `flask --app app db-upgrade` (the app also migrates at start under a lock) |
| CronJob `-purge` | `purge-old` daily (R1 retention) |
| Service | web on 8000; headless `-worker-metrics` on 9101 for Prometheus (OBS-02) |
| ConfigMap | non-secret settings from `values.config` (checksum annotation rolls the pods on change) |
| ServiceAccount | `automountServiceAccountToken: false`: the app never calls the Kubernetes API |
| PDB, HPA, Ingress | PDB `minAvailable: 1` with 2+ web pods; HPA and Traefik Ingress off by default (one node; the public entry is Cloudflare Tunnel, INF-08) |

Secrets are **not** in the chart: `secrets.web` / `secrets.worker` name existing Secrets
(INF-07 will create them from SOPS files). The image is taken **by digest** (`image.digest`,
signed by `build.yml`, CD-01); rendering without a digest or a test tag fails.

## Pod Security "restricted" in every pod

`runAsNonRoot` (uid/gid 10001), `allowPrivilegeEscalation: false`, `capabilities: drop ALL`,
`seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem: true`; writable `/tmp` is an
`emptyDir` with a size limit (the instance folder `DATA_DIR=/tmp/data`, `HOME=/tmp`, PDF
temp files, metrics files). Requests and memory limits on every container; no CPU limits
(throttling on a small node hurts more than it protects).

## Values per environment

- `deploy/envs/prod/values.yaml`: 2 web pods, 1 worker with 2 threads, HPA off (one node).
- `deploy/envs/staging/values.yaml`: 1 web pod, 1 worker with 1 thread, no PDB, its own clean-up time.

## Verification

- `helm lint --strict` and `helm template | kubeconform -strict` (Kubernetes 1.34) for prod,
  staging and with every switch on (HPA, Ingress): clean, locally and in `.github/workflows/chart.yml`.
- **The image under the same restrictions** (here, with Docker: read-only root, non-root
  10001, all capabilities dropped, no-new-privileges, tmpfs `/tmp`, production settings,
  PostgreSQL): migrations, gunicorn with 2 processes, the worker with its metrics port and
  `purge-old` work; `/healthz`, `/readyz` answer; `/metrics` adds up both gunicorn processes
  and is 404 with a Cloudflare header; `docker stop` ends web and worker with code 0.
  This run found a bug fixed here: in multi-process mode the metrics folder must exist before
  gunicorn's hooks run (`app/metrics.py` creates it at import; test added).
- `chart.yml` (on pull requests that touch the chart): builds the image, creates a kind
  cluster, installs PostgreSQL in its own namespace and the chart in a namespace with
  `pod-security.kubernetes.io/enforce=restricted`, checks `/readyz`, `/metrics` (open inside
  the cluster, 404 with a Cloudflare header), the worker's metrics, then runs a **rolling
  update while a pod requests a page every 0.1 s for 100 s: no 5xx at all, and requests that
  get no answer on the way at most 0.5 %** (two runs lost 1 of ~850 each that way; each loss is
  logged with the time and curl's exit code, and shown as a warning).
  kind cannot run in Claude's own environment, so the first real install is that CI run
  (its first run found that the migration hook ran before the ServiceAccount existed — fixed in INF-06).

## Not here

- NetworkPolicy — INF-06; Secrets from SOPS — INF-07; Ingress hosts — INF-08; the cluster
  itself — INF-02; PostgreSQL in the cluster with backups — INF-03.
- A Locust load test is part of PRD-01; the rolling-update check here uses steady requests.
