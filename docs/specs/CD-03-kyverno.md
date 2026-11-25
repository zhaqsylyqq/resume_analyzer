# CD-03 — Admission policies (Kyverno)

## What

| File | Rule (namespaces `hrscreen`, `hrscreen-staging`) |
|---|---|
| `deploy/platform/policies/verify-image-signature.yaml` | an image `ghcr.io/zhaqsylyqq/resume_analyzer*` must carry the cosign keyless signature of `build.yml` on `main` / `v*` (issuer GitHub Actions, transparency log Rekor); Kyverno pins it to the verified digest. `failurePolicy: Fail`: if Kyverno cannot check, new pods do not start in these namespaces |
| `restrict-image-registries.yaml` | only `ghcr.io/zhaqsylyqq/resume_analyzer@sha256:…` (containers, init and ephemeral containers) — Docker Hub and any other registry refused |
| `disallow-latest-tag.yaml` | no `:latest`, no image without a tag or digest |
| `require-requests-limits.yaml` | CPU and memory requests and a memory limit on every container (no CPU limit on purpose) |
| `disallow-host-access.yaml` | no `hostPath`, `hostNetwork`, `hostPID`, `hostIPC`, `hostPort` (also forbidden by Pod Security "restricted"; kept in case the namespace label is lost) |
| `deploy/platform/kyverno/values.yaml` | Kyverno 1.19.2 (chart 3.9.2), one replica per controller (one node); its webhooks skip `kube-system`, `kyverno`, `monitoring`, `argocd`, `cnpg-system`, so a broken Kyverno never blocks the platform |

Kyverno applies Pod rules to Deployments, Jobs and CronJobs too (auto-generated rules).

## Rollout: Audit for a week, then Enforce

All rules ship with `failureAction: Audit` (violations only reported: `kubectl get policyreports -A`).
After a week with no violations in the reports: change `Audit` to `Enforce` in the five
files and apply them (CD-02: through Argo CD).

## Verification

- `kyverno test deploy/platform/policies/tests` (offline, in CI): 19 checks — a good pod
  passes everything; Docker Hub, `:latest`, untagged, no limits, host network, host path are
  each refused by the right rule.
- The chart (prod values, by digest) passes all policies: `kyverno apply` → 20 pass, 0 fail.
- `chart.yml` in kind, after the app tests: installs Kyverno with these values, applies the
  policies in **Enforce**, then server-side dry runs in `hrscreen-staging` must be refused:
  an image from Docker Hub, our image by `:latest`, **our image name with a digest that
  build.yml never signed**, host network.

## Known open point

`build.yml` signs with cosign v3. Whether Kyverno 1.19 reads that signature format is proven
only when the first real image is published (after PR №2 is merged): then a pod with the
published digest must be **admitted** in staging. Until then the policies are in Audit, so a
format mismatch cannot block anything. If it does not verify, switch the rule to the GitHub
attestation (`type: SigstoreBundle`) that `build.yml` already attaches.
