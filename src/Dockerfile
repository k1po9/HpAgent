# syntax=docker/dockerfile:1

# 指定基础镜像：Python 3.11，使用 Debian slim 精简版
FROM python:3.11-slim

# 设置非交互式前端，避免构建时出现警告（debconf 报错）
ENV DEBIAN_FRONTEND=noninteractive

# 替换 Debian APT 源为国内源
RUN sed -i 's/deb.debian.org/mirror.iscas.ac.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装 Docker 环境
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    && install -m 0755 -d /etc/apt/keyrings \
    # 从中科院镜像下载 Docker GPG 密钥
    && curl -fsSL https://mirror.iscas.ac.cn/docker-ce/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    # 使用中科院的 Docker CE 安装源
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirror.iscas.ac.cn/docker-ce/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# 设定工作目录
WORKDIR /app

COPY requirements.txt .
# 使用 BuildKit 缓存 + 清华 PyPI 源
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 启动脚本
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]