#!/bin/bash
# 清理代理模式残留的 iptables 规则，然后启动 NapCat
iptables -t nat -D OUTPUT -m owner --uid-owner 1000 -p tcp -d 127.0.0.0/8 -j RETURN 2>/dev/null || true
iptables -t nat -D OUTPUT -m owner --uid-owner 1000 -p tcp -j REDIRECT --to-port 12345 2>/dev/null || true
exec bash /app/entrypoint.sh "$@"
