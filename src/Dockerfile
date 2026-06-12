# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# 替换 Debian APT 源为国内源（加速构建）
RUN sed -i 's/deb.debian.org/mirror.iscas.ac.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装运行时依赖 + nsjail（OS 级沙箱隔离执行）+ Node.js（stock-sdk MCP）
RUN apt-get update -o Acquire::Check-Valid-Until=false && apt-get install -y \
    ca-certificates \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# 安装 nsjail（从官方 GitHub 下载静态二进制文件）
ARG NSJAIL_MIRROR=https://ghfast.top/
RUN curl -L --retry 3 --retry-delay 10 \
    -o /usr/local/bin/nsjail \
    ${NSJAIL_MIRROR}https://github.com/google/nsjail/releases/download/3.4/nsjail-3.4-x86_64 \
    && chmod +x /usr/local/bin/nsjail

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
