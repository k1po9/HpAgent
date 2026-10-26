# PG16 only. Registry alias serves the same verified Docker Hub manifest.
FROM registry.hub.docker.com/groonga/pgroonga:4.0.8-debian-16@sha256:24bc15d68143620bf21eb292d905aa5e9bcff120fd09032f119ab316b38cd19a
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       postgresql-16-pgvector=0.8.6-1.pgdg13+1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
