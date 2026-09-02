#!/bin/sh
set -eu

template=/etc/searxng/settings.template.yml
rendered=/tmp/hpagent-searxng-settings.yml

/usr/local/searxng/.venv/bin/python - "$template" "$rendered" <<'PY'
import os
import sys
from pathlib import Path

import yaml

source, target = map(Path, sys.argv[1:])
settings = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
outgoing = settings.setdefault("outgoing", {})
proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
if proxy:
    outgoing["proxies"] = {"all://": [proxy]}
else:
    outgoing.pop("proxies", None)
target.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
PY

export SEARXNG_SETTINGS_PATH="$rendered"
exec /usr/local/searxng/entrypoint.sh
