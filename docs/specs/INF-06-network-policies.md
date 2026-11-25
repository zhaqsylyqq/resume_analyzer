# INF-06 — Network policies

## What

`deploy/chart/templates/networkpolicy.yaml` (on by default, `networkPolicy.enabled`):

| Policy | Allows |
|---|---|
| `-default-deny` | nothing, in or out, for every pod of the namespace |
| `-dns` | every pod of the app → CoreDNS (kube-system, `k8s-app=kube-dns`) on 53 UDP/TCP |
| `-database` | every pod of the app (web, worker, migration and clean-up jobs) → the database peers (`networkPolicy.database.to`, default: CloudNativePG cluster `pg` in the same namespace) on 5432 |
| `-web-in` | → web on 8000 from Traefik (kube-system), from a `cloudflared` pod in the namespace, and from the `monitoring` namespace (Prometheus, OBS-02) |
| `-worker-in` | → worker on 9101 from `monitoring` only |
| `-ai` | worker (and web while `webCallsAI: true`) → any **public** address on 443; private ranges, the cluster, CGNAT and the cloud metadata address `169.254.0.0/16` excluded |

So: a pod from another namespace (staging, or anything else) cannot open the web pods; the
worker cannot reach web, the Kubernetes API or anything private; nothing leaves on ports
other than 443/53/5432; jobs reach only DNS and the database.

## Limits (accepted, from the plan)

- Standard NetworkPolicy cannot filter by domain: "only the AI provider" is approximated by
  "public addresses on 443". Exact domain allow-listing needs Cilium or an egress proxy.
- `webCallsAI: true` by default: the vacancy bias check still calls the AI from web (INF-07
  gap). After that call moves to the worker, set it to `false` — web then has no way out
  except DNS and the database (the DoD "web cannot open a connection to the internet").

## Verification

- `helm lint --strict`, `kubeconform` for prod and staging (15 / 14 objects).
- `chart.yml` in kind (kind ≥ 0.24 enforces NetworkPolicy): installs with `webCallsAI: false`
  and the test database in its own namespace, then checks this matrix from inside the pods
  (python socket, 4 s) and writes it to the run summary:

  | Path | Expected |
  |---|---|
  | worker → internet :443 | allowed |
  | worker → internet :80 | denied |
  | worker → Kubernetes API | denied |
  | worker → web :8000 | denied |
  | worker → database | allowed |
  | web → internet :443 | denied |
  | web → database | allowed |
  | a pod in another namespace → web | denied |

  The page checks and the rolling-update traffic run from the `monitoring` namespace, which is
  allowed; they still pass with the policies on.

## Found by the first kind run (INF-05 follow-up, fixed here)

The first `chart.yml` run (on INF-07) failed at install: the migration Job is a pre-install
hook, so it runs before the chart's ServiceAccount exists, and Kubernetes refused its pod
("serviceaccount hrscreen not found"). The hook now uses the namespace's `default` account
(still with no token mounted). Everything before the install step was green, including the
SOPS path and the dry run of `build.yml`.
