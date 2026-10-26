# Separate developer runner; never install verification tools in business containers.
FROM hpagent/hpagent:offline
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir \
       pytest==8.4.2 pytest-asyncio==0.26.0 ruff==0.17.1 mypy==1.20.2
WORKDIR /repo
ENTRYPOINT ["python"]
