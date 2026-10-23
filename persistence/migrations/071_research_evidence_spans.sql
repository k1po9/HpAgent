-- Multiple immutable, precisely located excerpts may belong to the same source.
SET search_path TO hpagent, public;
ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_research_id_source_id_key;
CREATE UNIQUE INDEX uq_evidence_items__source_span ON evidence_items
 (research_id,source_id,content_ref,(source_locator->'text_span'->>'start'),
  (source_locator->'text_span'->>'end'));
