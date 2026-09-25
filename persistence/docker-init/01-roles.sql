-- 全新 PostgreSQL 数据目录初始化时执行。
-- 密码由 Compose 注入容器环境。

\getenv api_password HPAGENT_API_PASSWORD
\getenv worker_password HPAGENT_WORKER_PASSWORD

SELECT format(
    'CREATE ROLE hpagent_api LOGIN PASSWORD %L
     NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
    :'api_password'
) \gexec

SELECT format(
    'CREATE ROLE hpagent_worker LOGIN PASSWORD %L
     NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
    :'worker_password'
) \gexec