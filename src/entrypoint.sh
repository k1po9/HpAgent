#!/bin/bash
set -e  # 任何一条命令失败就立刻退出，防止带着错误继续跑

echo "===== 启动内部 Docker 守护进程 ====="
# 后台启动 dockerd，日志写到文件里
nohup dockerd > /var/log/dockerd.log 2>&1 &

echo "等待 Docker 守护进程就绪..."
# 循环执行 docker info，直到成功才继续
# dockerd 启动需要几十毫秒到一两秒，如果不等它，直接跑 Python 代码，代码里调用 docker.from_env() 时会因为连不上守护进程而报错。
until docker info > /dev/null 2>&1; do
    sleep 1
done

echo "内部 Docker 守护进程已启动！"

# 最后启动业务应用
cd /app
exec python -u -m main
