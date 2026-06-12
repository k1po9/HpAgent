FROM mlikiowa/napcat-docker:latest

# ============================================================
# NapCat + redsocks 透明代理（iptables TPROXY → SOCKS5）
#
# 为什么不用 proxychains（LD_PRELOAD）：
#   - proxychains 在每次 socket connect() 调用时都有用户态 hook 开销
#   - QQ NT 内核的 OIDB RPC 超时只有 3 秒，叠加代理延迟后极易超时
#   - redsocks + iptables 在内核 NAT 层重定向，每连接零额外开销
# ============================================================

RUN apt-get update \
    && apt-get install -y --fix-broken \
    && apt-get install -y --no-install-recommends \
        redsocks \
        iptables \
        proxychains4 \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY redsocks.conf /etc/redsocks.conf
COPY proxychains.conf /etc/proxychains4.conf
COPY entrypoint.sh /napcat-entrypoint.sh
RUN chmod +x /napcat-entrypoint.sh

ENTRYPOINT ["bash", "/napcat-entrypoint.sh"]
CMD []
