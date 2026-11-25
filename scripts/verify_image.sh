#!/usr/bin/env bash
# CD-01: check that an image was built and signed by this repository's build workflow.
#
#   scripts/verify_image.sh ghcr.io/zhaqsylyqq/resume_analyzer@sha256:<digest>
#
# Passes only when (1) the cosign signature was made by .github/workflows/build.yml of
# zhaqsylyqq/resume_analyzer running on main or a v* tag (GitHub OIDC, keyless), (2) a GitHub
# build-provenance attestation and (3) an SBOM attestation exist for the same digest, both
# from the same workflow. Any other image - unsigned, or signed by someone else - fails.
# Needs cosign (v2.4+) and gh (with GH_TOKEN, or `gh auth login`). Always pass a digest:
# a tag can be moved, a digest cannot.
set -euo pipefail

ref="${1:?usage: $0 <image>@sha256:<digest>}"
repo="${VERIFY_REPO:-zhaqsylyqq/resume_analyzer}"
workflow="https://github.com/${repo}/.github/workflows/build.yml"
identity="^$(printf '%s' "$workflow" | sed 's/[.]/\\./g')@refs/(heads/main|tags/v[0-9].*)$"

case "$ref" in
  *@sha256:*) ;;
  *) echo "verify_image: give the image by digest (…@sha256:…), not by tag" >&2; exit 2 ;;
esac

echo "1/3 cosign signature from ${workflow}"
cosign verify "$ref" \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com >/dev/null

echo "2/3 build provenance attestation"
gh attestation verify "oci://${ref}" --repo "$repo" \
  --signer-workflow "${repo}/.github/workflows/build.yml" \
  --predicate-type https://slsa.dev/provenance/v1 >/dev/null

echo "3/3 SBOM attestation"
gh attestation verify "oci://${ref}" --repo "$repo" \
  --signer-workflow "${repo}/.github/workflows/build.yml" \
  --predicate-type https://cyclonedx.org/bom >/dev/null

echo "OK: ${ref} was built, signed and attested by ${repo} CI"
