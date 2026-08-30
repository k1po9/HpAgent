#!/usr/bin/env bash
# Build, collect, and export every image required by the selected Compose profiles.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "[offline-export] ERROR: $*" >&2
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
[[ ! -e "$archive" ]] || fail "refusing to overwrite existing file: $archive"
mkdir -p "$(dirname "$archive")"

compose_args=()
for profile in "${profiles[@]}"; do
  compose_args+=(--profile "$profile")
done

echo "[offline-export] Pulling third-party images for profiles: ${profiles[*]}"
docker compose "${compose_args[@]}" pull --ignore-buildable

echo "[offline-export] Building HpAgent images"
docker compose "${compose_args[@]}" build

mapfile -t images < <(
  docker compose "${compose_args[@]}" config --images | sed '/^[[:space:]]*$/d' | sort -u
)
(( ${#images[@]} > 0 )) || fail "Compose resolved no images"

for image in "${images[@]}"; do
  docker image inspect "$image" >/dev/null 2>&1 || fail "image is missing after pull/build: $image"
done

echo "[offline-export] Exporting ${#images[@]} images to $archive"
docker image save --output "$archive" "${images[@]}"

manifest="${archive}.manifest.txt"
checksum="${archive}.sha256"
{
  echo "archive=$(basename "$archive")"
  echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "profiles=${profiles[*]}"
  printf 'image=%s\n' "${images[@]}"
} > "$manifest"

if command -v sha256sum >/dev/null 2>&1; then
  (
    cd "$(dirname "$archive")"
    sha256sum "$(basename "$archive")" > "$(basename "$checksum")"
  )
else
  echo "[offline-export] WARNING: sha256sum is unavailable; checksum not written" >&2
fi

echo "[offline-export] Done"
echo "  archive:  $archive"
echo "  manifest: $manifest"
[[ -f "$checksum" ]] && echo "  checksum: $checksum"
