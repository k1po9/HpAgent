-- File Assistant F1: persisted normalized documents for the dedicated Docling Worker.
SET search_path TO hpagent, public;

CREATE TABLE normalized_documents (
  document_ref varchar(240) PRIMARY KEY,
  operation_id varchar(200) NOT NULL UNIQUE,
  account_id uuid NOT NULL,
  run_id uuid NOT NULL,
  file_id uuid NOT NULL,
  normalized_document jsonb NOT NULL,
  block_count integer NOT NULL,
  table_count integer NOT NULL,
  truncated boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_normalized_documents__run_file FOREIGN KEY(run_id,file_id)
    REFERENCES run_files(run_id,file_id) ON DELETE CASCADE,
  CONSTRAINT ck_normalized_documents__operation CHECK(length(btrim(operation_id)) > 0),
  CONSTRAINT ck_normalized_documents__json CHECK(jsonb_typeof(normalized_document)='object'),
  CONSTRAINT ck_normalized_documents__counts CHECK(block_count >= 0 AND table_count >= 0)
);
CREATE INDEX ix_normalized_documents__run_file
  ON normalized_documents(run_id,file_id,created_at DESC);

GRANT SELECT,INSERT ON normalized_documents TO hpagent_worker;
GRANT SELECT ON normalized_documents TO hpagent_api;
