-- Explicit -v schema_name=hpagent or public, on a quiescent dedicated target.
-- All row bytes stay in PostgreSQL; output contains only counts and SHA256.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
SELECT format(
    'SELECT %L AS relation, count(*) AS rows, encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text, E''\n'' ORDER BY to_jsonb(t)::text), ''''), ''UTF8'')), ''hex'') AS rows_sha256 FROM %I.%I AS t',
    schemaname || '.' || tablename, schemaname, tablename)
FROM pg_tables WHERE schemaname = :'schema_name' ORDER BY tablename
\gexec
COMMIT;
