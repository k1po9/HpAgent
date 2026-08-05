from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from .conftest import account

pytestmark = pytest.mark.postgres


def test_db_001_active_identity_is_unique(db, account_id):
    db.execute("INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,normalized_subject_id,verified_at) VALUES (%s,%s,'web','one','one',now())", (uuid4(), account_id))
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,normalized_subject_id,verified_at) VALUES (%s,%s,'web','two','one',now())", (uuid4(), account(db)))


def test_db_014_auth_binding_cannot_cross_account(db, account_id):
    other = account(db)
    binding = uuid4()
    db.execute("INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,normalized_subject_id,verified_at) VALUES (%s,%s,'web','a','a',now())", (binding, account_id))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("INSERT INTO web_auth_sessions(web_auth_session_id,account_id,identity_binding_id,token_hash,csrf_secret_hash,expires_at,idle_expires_at) VALUES (%s,%s,%s,'a','b',now()+interval '1 day',now()+interval '1 hour')", (uuid4(), other, binding))


def test_db_017_deferred_trigger_rejects_run_without_agent_message(db, account_id, database_url):
    conversation, session, user, run = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(database_url, autocommit=False) as tx:
        tx.execute("SET search_path TO hpagent, public")
        tx.execute("INSERT INTO conversations(conversation_id,account_id,last_message_seq) VALUES (%s,%s,1)", (conversation, account_id))
        tx.execute("INSERT INTO sessions(session_id,account_id,conversation_id,sequence) VALUES (%s,%s,%s,1)", (session, account_id, conversation))
        tx.execute("INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,sequence,client_request_id) VALUES (%s,%s,%s,'user','accepted','hello',1,%s)", (user, account_id, conversation, uuid4()))
        tx.execute("INSERT INTO runs(run_id,account_id,conversation_id,session_id,trigger_message_id,workflow_id,context_message_seq) VALUES (%s,%s,%s,%s,%s,%s,1)", (run, account_id, conversation, session, user, f"hpagent-web-run-{run}"))
        with pytest.raises(psycopg.errors.RaiseException):
            tx.commit()


def test_db_018_pending_message_cannot_be_completed(db, account_id):
    conversation = uuid4()
    db.execute("INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s)", (conversation, account_id))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO messages(message_id,account_id,conversation_id,role,status,sequence,produced_by_run_id,completed_at) VALUES (%s,%s,%s,'assistant','pending',1,%s,now())", (uuid4(), account_id, conversation, uuid4()))


def test_db_023_sequence_does_not_change_metadata_version(db, account_id):
    conversation = uuid4()
    db.execute("INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s)", (conversation, account_id))
    db.execute("UPDATE conversations SET last_message_seq=last_message_seq+2,updated_at=now() WHERE conversation_id=%s", (conversation,))
    row = db.execute("SELECT last_message_seq,metadata_version FROM conversations WHERE conversation_id=%s", (conversation,)).fetchone()
    assert row == (2, 1)
