#!/usr/bin/env bash
# INF-07: secrets of an environment, kept in Git encrypted with SOPS + age.
#
#   scripts/secrets.sh keygen                 make your age key (once per person), print its public key
#   scripts/secrets.sh encrypt <env>          deploy/envs/<env>/secrets.yaml -> secrets.enc.yaml (plain copy removed)
#   scripts/secrets.sh edit <env>             edit the encrypted file in place ($EDITOR; decrypted only in memory/tmp)
#   scripts/secrets.sh apply <env> [ns]       decrypt and `kubectl apply` into the namespace (default: hrscreen / hrscreen-staging)
#   scripts/secrets.sh check                  every deploy/envs/*/secrets.enc.yaml is really encrypted (CI runs this)
#
# The private key lives in ~/.config/sops/age/keys.txt (or SOPS_AGE_KEY_FILE). Rotation and
# adding/removing a key holder: docs/runbooks/secrets-rotation.md.
set -euo pipefail
cd "$(dirname "$0")/.."

envdir() {
  case "${1:-}" in prod|staging) echo "deploy/envs/$1" ;; *) echo "environment must be prod or staging" >&2; exit 2 ;; esac
}

case "${1:-}" in
  keygen)
    keyfile="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
    if [ -s "$keyfile" ]; then echo "You already have a key in $keyfile:"; age-keygen -y "$keyfile"; exit 0; fi
    mkdir -p "$(dirname "$keyfile")"; (umask 077; age-keygen -o "$keyfile" 2>/dev/null)
    echo "Private key: $keyfile  (keep a copy offline; never commit or send it)"
    echo "Public key (add to .sops.yaml):"; age-keygen -y "$keyfile"
    ;;
  encrypt)
    dir="$(envdir "${2:-}")"; plain="$dir/secrets.yaml"; enc="$dir/secrets.enc.yaml"
    [ -f "$plain" ] || { echo "fill $plain first (copy deploy/secrets/secrets.example.yaml)" >&2; exit 1; }
    sops --encrypt --filename-override "$enc" "$plain" > "$enc.tmp" && mv "$enc.tmp" "$enc"
    rm -f "$plain"; echo "Wrote $enc; the plain copy is deleted. Commit $enc."
    ;;
  edit)
    sops "$(envdir "${2:-}")/secrets.enc.yaml"
    ;;
  apply)
    env="${2:-}"; dir="$(envdir "$env")"
    ns="${3:-$([ "$env" = prod ] && echo hrscreen || echo hrscreen-staging)}"
    sops --decrypt "$dir/secrets.enc.yaml" | kubectl apply -n "$ns" -f -
    ;;
  check)
    python3 scripts/check_secrets.py deploy/envs/*/secrets*.yaml
    ;;
  *)
    sed -n '2,13p' "$0"; exit 2
    ;;
esac
