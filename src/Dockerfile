# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# 替换 Debian APT 源为国内源（加速构建）
RUN sed -i 's/deb.debian.org/mirror.iscas.ac.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装运行时依赖 + nsjail（OS 级沙箱隔离执行）
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    nsjail \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# runner.py 放到独立路径，避免和 nsjail bind mount 的 /work 冲突
RUN cp /app/sandbox/runner.py /opt/nsjail-runner.py

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
