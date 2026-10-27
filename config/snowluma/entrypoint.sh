#!/bin/sh
set -eu
node /opt/hpagent-snowluma/verify.cjs
exec /root/start.sh
