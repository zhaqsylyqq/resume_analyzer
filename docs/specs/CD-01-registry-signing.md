# CD-01 — Registry, signature and provenance

## Why

Until now the image existed only inside CI runs and on the laptop. A cluster (INF/CD stages)
needs to pull it from a registry, and it must be able to tell an image our CI built from any
other image with the same name (threat T-10: compromised dependency, base image or build).

## How

`.github/workflows/build.yml`, job **Build, sign, attest**:

1. Triggers: push to `main` (that is, a merged PR), a `v*` tag, manual run; pull requests that
   change the workflow, the Dockerfile or `requirements.lock` run a **dry run** (build and scan,
   nothing pushed). Only `main` and `v*` publish (`PUBLISH`).
2. Build once with the GitHub Actions cache, load locally and **scan with Trivy** (fixed
   HIGH/CRITICAL vulnerabilities and secrets; report-only until 2026-12-01 like the other scanners).
3. Push to **GHCR** `ghcr.io/zhaqsylyqq/resume_analyzer` with tags `sha-<commit>`, the branch
   name (`main`) and the version of a `v*` tag. Buildx's own provenance/SBOM are off, so a
   digest is one plain manifest; both are attached as GitHub attestations instead.
4. **cosign keyless signature** of the digest: GitHub's OIDC token proves "this repository,
   this workflow, this ref"; Sigstore's Fulcio issues a 10-minute certificate, the signature
   goes to the Rekor transparency log. There is no private key to keep or leak.
5. **Attestations** (stored by GitHub and pushed next to the image): build provenance
   (`https://slsa.dev/provenance/v1`, SLSA Build L2) and the CycloneDX SBOM of the published digest.
6. **Self-check**: `scripts/verify_image.sh` on the new digest must pass, and on the official
   `postgres:16` image (signed by nobody here) must fail. The digest and tags go to the run summary.

Permissions are given to this job only: `packages: write`, `id-token: write`, `attestations: write`;
the rest of CI keeps read-only tokens.

## Checking an image by hand

```
scripts/verify_image.sh ghcr.io/zhaqsylyqq/resume_analyzer@sha256:<digest>
```

Needs `cosign` and `gh` (logged in). It refuses a tag (only digests), then checks: the cosign
certificate identity is exactly
`https://github.com/zhaqsylyqq/resume_analyzer/.github/workflows/build.yml@refs/heads/main`
(or `@refs/tags/v…`) with issuer `https://token.actions.githubusercontent.com`; a provenance
attestation and an SBOM attestation signed by the same workflow. A branch other than `main`,
a fork or another workflow does not match.

## "No images built outside CI"

- Nobody pushes by hand: no personal token with `write:packages` is used for this package;
  the workflow's `GITHUB_TOKEN` is the only writer.
- Even if someone did, the image would carry no signature from `build.yml@main` and
  `verify_image.sh` refuses it; CD-03 (Kyverno) will make the cluster refuse to run it.

## After the first publish (repository owner)

- GHCR creates the package on the first push from `main`, **private by default**. Either make
  it public (Package settings → Change visibility; the repository is public anyway, and Trivy
  has checked the image has no secrets) or give the cluster a pull secret (INF stage).
- Package settings → "Manage Actions access": only `zhaqsylyqq/resume_analyzer` with write.

## Verification

- `actionlint` on the workflow: no findings.
- `verify_image.sh`: refuses a tag (exit 2) and an unsigned foreign image (`no signatures found`);
  the certificate-identity pattern accepts `main` and `v*` of this repository's `build.yml` and
  rejects another branch and another owner.
- The real publish, signature and attestations run for the first time when PR №2 is merged into
  `main` (the dry run already runs on the PR). Then: the run summary shows the digest, and
  `verify_image.sh <digest>` passes on any computer.
