#!/usr/bin/env bash
# Load an offline image archive and verify that Compose can resolve it without pulls/builds.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "[offline-load] ERROR: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"

archive="${1:-dist/hpagent-offline-images.tar}"
if (( $# > 0 )); then
  shift
fi
profiles=("${@:-web-prod}")

[[ "$archive" = /* ]] || archive="$ROOT/$archive"
[[ -f "$archive" ]] || fail "archive not found: $archive"

checksum="${archive}.sha256"
if [[ -f "$checksum" ]]; then
  command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required to verify $checksum"
  echo "[offline-load] Verifying checksum"
  (
    cd "$(dirname "$archive")"
    sha256sum --check "$(basename "$checksum")"
  )
fi

echo "[offline-load] Loading $archive"
docker image load --input "$archive"

compose_args=()
for profile in "${profiles[@]}"; do
  compose_args+=(--profile "$profile")
done

mapfile -t images < <(
  docker compose "${compose_args[@]}" config --images | sed '/^[[:space:]]*$/d' | sort -u
)
(( ${#images[@]} > 0 )) || fail "Compose resolved no images"

missing=()
for image in "${images[@]}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    missing+=("$image")
  fi
done

if (( ${#missing[@]} > 0 )); then
  printf '[offline-load] Missing image: %s\n' "${missing[@]}" >&2
  fail "archive does not contain every image required by profiles: ${profiles[*]}"
fi

echo "[offline-load] Verified ${#images[@]} Compose images"
printf '  %s\n' "${images[@]}"
echo
echo "Start without network pulls or local builds:"
printf '  docker compose'
for profile in "${profiles[@]}"; do
  printf ' --profile %q' "$profile"
done
printf ' up -d --no-build --pull never\n'
