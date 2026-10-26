-- Run with psql -X -v ON_ERROR_STOP=1 against the explicit active memory DB.
-- This proves semantic emptiness only. Stopped senders/API are separate facts.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL search_path = public, pg_catalog;
DO $$
DECLARE
    relation_name text;
    rows_found bigint;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'documents', 'chunks', 'memory_units', 'invalidated_memory_units',
        'mental_models', 'async_operations', 'graph_maintenance_queue',
        'entity_maintenance_queue', 'attachments', 'file_storage',
        'mental_model_history', 'observation_history', 'knowledge_pages',
        'directives', 'webhooks', 'bank_aliases', 'entities', 'unit_entities',
        'memory_links', 'entity_cooccurrences'
    ] LOOP
        EXECUTE format('SELECT count(*) FROM public.%I', relation_name) INTO rows_found;
        IF rows_found <> 0 THEN
            RAISE EXCEPTION 'empty_target_rejected: % has % rows', relation_name, rows_found;
        END IF;
    END LOOP;
    SELECT count(*) INTO rows_found FROM public.llm_requests
      WHERE status IS DISTINCT FROM 'success';
    IF rows_found <> 0 THEN
        RAISE EXCEPTION 'empty_target_rejected: unfinished llm_requests %', rows_found;
    END IF;
END $$;
SELECT 'empty_target' AS result, current_database() AS database,
       (SELECT count(*) FROM public.banks) AS banks,
       (SELECT count(*) FROM public.llm_requests) AS completed_request_logs;
COMMIT;
