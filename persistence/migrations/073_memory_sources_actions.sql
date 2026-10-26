-- Source permission and durable remote-operation identity; no historical import.
-- Reserved only; scoped context invalidation is implemented in the next stage.
ALTER TABLE accounts ADD COLUMN memory_epoch bigint NOT NULL DEFAULT 0 CHECK(memory_epoch>=0);
CREATE TABLE memory_sources (
 source_id uuid PRIMARY KEY, account_id uuid NOT NULL,
 source_kind text NOT NULL CHECK(source_kind IN ('chat','work','promotion')),
 source_key text NOT NULL, origin_run_id uuid NOT NULL,
 work_id uuid, result_ref_id uuid, parent_source_id uuid,
 document_id text NOT NULL, scope text[] NOT NULL,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0), source_hash char(64) NOT NULL,
 policy_version text NOT NULL, generation bigint NOT NULL DEFAULT 1 CHECK(generation>0),
 state text NOT NULL CHECK(state IN ('active','blocked','forgotten','quarantined')),
 manual_curation boolean NOT NULL DEFAULT false, current_action_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_id,source_id), UNIQUE(account_id,document_id),
 UNIQUE(account_id,source_kind,origin_run_id,source_key),
 FOREIGN KEY(account_id,origin_run_id) REFERENCES runs(account_id,run_id),
 FOREIGN KEY(account_id,work_id,origin_run_id) REFERENCES runs(account_id,work_id,run_id),
 FOREIGN KEY(account_id,result_ref_id) REFERENCES capability_result_refs(account_id,ref_id),
 FOREIGN KEY(account_id,parent_source_id) REFERENCES memory_sources(account_id,source_id),
 CHECK(array_position(scope,NULL) IS NULL),
 CHECK(scope=ARRAY['scope:private'] OR
   (cardinality(scope)=2 AND scope[1]='scope:group' AND scope[2] LIKE 'group:_%') OR
   (work_id IS NOT NULL AND scope=ARRAY['scope:work:' || work_id::text])),
 CHECK(source_kind<>'work' OR (work_id IS NOT NULL AND result_ref_id IS NOT NULL)),
 CHECK(source_kind<>'promotion' OR parent_source_id IS NOT NULL)
);
CREATE TABLE memory_actions (
 action_id uuid PRIMARY KEY, account_id uuid NOT NULL, source_id uuid NOT NULL,
 generation bigint NOT NULL CHECK(generation>0),
 kind text NOT NULL CHECK(kind IN ('retain','correct','invalidate','restore','delete','replace','reprocess')),
 actor text NOT NULL, reason text NOT NULL,
 action_key text NOT NULL, request_hash char(64) NOT NULL, request_payload jsonb,
 target_memory_id text, operation_id uuid,
 invalidation_epoch bigint, affected_scopes jsonb,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','submitted','verifying','verified','uncertain','failed')),
 failure_count integer NOT NULL DEFAULT 0 CHECK(failure_count>=0), error_code text,
 verification jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), verified_at timestamptz,
 UNIQUE(account_id,action_id), UNIQUE(account_id,source_id,action_id),
 UNIQUE(account_id,action_key), UNIQUE(operation_id),
 FOREIGN KEY(account_id,source_id) REFERENCES memory_sources(account_id,source_id),
 CHECK(kind<>'retain' OR operation_id IS NOT NULL),
 CHECK(request_payload IS NULL OR jsonb_typeof(request_payload)='object'),
 CHECK(invalidation_epoch IS NULL OR invalidation_epoch>0)
);
ALTER TABLE memory_sources ADD FOREIGN KEY(account_id,source_id,current_action_id)
 REFERENCES memory_actions(account_id,source_id,action_id);
CREATE INDEX ix_memory_actions_source ON memory_actions(account_id,source_id,created_at);
ALTER TABLE outbox_events DROP CONSTRAINT ck_outbox_events__event_type;
ALTER TABLE outbox_events ADD CONSTRAINT ck_outbox_events__event_type CHECK(event_type IN
 ('start_run','cancel_run','retain_memory','memory_action','publish_terminal_event','publish_work_event','file_action_approval_decided'));
-- API needs source/status reads only in this stage, never frozen request text.
GRANT SELECT ON memory_sources TO hpagent_api;
GRANT SELECT(action_id,account_id,source_id,generation,kind,actor,reason,action_key,request_hash,
 target_memory_id,operation_id,invalidation_epoch,affected_scopes,status,failure_count,error_code,
 verification,created_at,updated_at,verified_at) ON memory_actions TO hpagent_api;
GRANT SELECT,INSERT,UPDATE ON memory_sources,memory_actions TO hpagent_worker;
