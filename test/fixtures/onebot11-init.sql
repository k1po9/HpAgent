\set ON_ERROR_STOP on
CREATE ROLE hpagent_api LOGIN PASSWORD 'fixture-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE hpagent_worker LOGIN PASSWORD 'fixture-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER ROLE hpagent_api IN DATABASE hpagent_onebot_test SET search_path=hpagent,public;
ALTER ROLE hpagent_worker IN DATABASE hpagent_onebot_test SET search_path=hpagent,public;
