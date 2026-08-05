-- Local-development roles. Production credentials must be injected by the deployer.
CREATE ROLE hpagent_api LOGIN PASSWORD 'hpagent_api' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE hpagent_worker LOGIN PASSWORD 'hpagent_worker' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
