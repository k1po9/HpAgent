#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose --profile web build hpagent-migrate hpagent-api hpagent
checksums=()
for service in hpagent-migrate hpagent-api hpagent; do
  image="$(docker compose --profile web config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"][sys.argv[1]]["image"])' "$service")"
  checksums+=("$(docker run --rm --entrypoint python "$image" -c '
from hashlib import sha256
from persistence.migrate import _migration_files
root, files = _migration_files()
assert str(root) == "/app/persistence/migrations", root
assert len(files) == 53, len(files)
print(sha256("".join(f"{p.name}:{sha256(p.read_bytes()).hexdigest()}\n" for p in files).encode()).hexdigest())
')")
  echo "$service: ${checksums[-1]}"
done
test "${checksums[0]}" = "${checksums[1]}"
test "${checksums[1]}" = "${checksums[2]}"
