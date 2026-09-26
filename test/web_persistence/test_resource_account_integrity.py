from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from conversation_domain.commands import CommandService
from workspace.catalog import WorkspaceCatalog
from workspace.resources import ResourcePolicy

pytestmark = pytest.mark.postgres


def test_direct_sql_rejects_cross_account_resource_ids(db, database_url):
    first, second = uuid4(), uuid4()
    for account in (first, second):
        db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account,))
    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url)
    policy = ResourcePolicy(database_url)
    first_tree = catalog.initialize(first)
    second_tree = catalog.initialize(second)
    first_node = UUID(first_tree["root_id"])
    second_node = UUID(second_tree["root_id"])
    first_conversation = UUID(commands.create_conversation(first, str(uuid4()))["conversation_id"])
    second_conversation = UUID(commands.create_conversation(second, str(uuid4()))["conversation_id"])
    first_run = UUID(commands.send_message(first, first_conversation,
                                           str(uuid4()), "read")["run_id"])
    second_run = UUID(commands.send_message(second, second_conversation,
                                            str(uuid4()), "read")["run_id"])
    own_grant = UUID(policy.grant(first, "conversation", first_conversation,
                                 first_node, ["list_metadata"], True)[0])
    foreign_grant = UUID(policy.grant(second, "conversation", second_conversation,
                                     second_node, ["read_content"], True)[0])
    file_id = uuid4()
    db.execute("INSERT INTO stored_files(file_id,account_id,source_workspace_id,purpose,"
               "status,original_name,display_name,storage_key,content_type,size_bytes,sha256,"
               "ready_at) VALUES (%s,%s,%s,'input','ready','own.txt','own.txt','integrity',"
               "'text/plain',1,%s,now())",
               (file_id, first, UUID(first_tree["workspace_id"]), "a" * 64))
    foreign_file = uuid4()
    db.execute("INSERT INTO stored_files(file_id,account_id,source_workspace_id,purpose,"
               "status,original_name,display_name,storage_key,content_type,size_bytes,sha256,"
               "ready_at) VALUES (%s,%s,%s,'input','ready','foreign.txt','foreign.txt',"
               "'integrity-foreign','text/plain',1,%s,now())",
               (foreign_file, second, UUID(second_tree["workspace_id"]), "b" * 64))
    with pytest.raises(psycopg.Error):
        db.execute("INSERT INTO resource_grants(grant_id,account_id,subject_kind,subject_id,"
                   "node_id,operation) VALUES (%s,%s,'conversation',%s,%s,'list_metadata')",
                   (uuid4(), first, first_conversation, second_node))
    with pytest.raises(psycopg.Error):
        db.execute("INSERT INTO resource_policy_versions(account_id,subject_kind,subject_id) "
                   "VALUES (%s,'conversation',%s)", (first, second_conversation))
    with pytest.raises(psycopg.Error):
        db.execute("INSERT INTO run_resource_candidates(run_id,account_id,node_id,"
                   "logical_name,display_name) VALUES (%s,%s,%s,'foreign','foreign')",
                   (first_run, first, second_node))
    with pytest.raises(psycopg.Error):
        db.execute("UPDATE run_resource_snapshots SET subject_id=%s WHERE run_id=%s",
                   (second_conversation, first_run))
    db.execute("INSERT INTO run_resource_candidates(run_id,account_id,node_id,"
               "logical_name,display_name) VALUES (%s,%s,%s,'own','own')",
               (first_run, first, first_node))
    with pytest.raises(psycopg.Error):
        db.execute("UPDATE run_resource_candidates SET fixed_file_id=%s "
                   "WHERE run_id=%s AND node_id=%s", (foreign_file, first_run, first_node))
    with pytest.raises(psycopg.Error):
        db.execute("INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,"
                   "node_id,basis,grant_id) VALUES (%s,%s,%s,%s,%s,'workspace_grant',%s)",
                   (uuid4(), first, first_run, file_id, second_node, own_grant))
    with pytest.raises(psycopg.Error):
        db.execute("INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,"
                   "node_id,basis,grant_id) VALUES (%s,%s,%s,%s,%s,'workspace_grant',%s)",
                   (uuid4(), first, first_run, file_id, first_node, foreign_grant))
    with pytest.raises(psycopg.Error):
        db.execute("INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,"
                   "basis) VALUES (%s,%s,%s,%s,'explicit_attachment')",
                   (uuid4(), first, second_run, file_id))
