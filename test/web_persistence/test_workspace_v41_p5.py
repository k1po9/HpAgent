"""P5 discovery must keep owner and frozen Agent scopes separate."""
from uuid import UUID, uuid4

import pytest

from conversation_domain.commands import CommandService
from workspace.catalog import WorkspaceCatalog
from workspace.discovery import WorkspaceDiscovery
from workspace.resources import ResourcePolicy

pytestmark = pytest.mark.postgres


def test_search_retention_and_run_authority(db, account_id, database_url):
    catalog = WorkspaceCatalog(database_url)
    root = UUID(catalog.initialize(account_id)["root_id"])
    conversation = UUID(CommandService(database_url).create_conversation(
        account_id, str(uuid4()))["conversation_id"])
    file_id = uuid4()
    db.execute("INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
               "original_name,display_name,storage_key,content_type,size_bytes,sha256,ready_at) "
               "VALUES (%s,%s,%s,'input','ready','report.pdf','report.pdf','p5-test',"
               "'application/pdf',7,%s,now())", (file_id, account_id, conversation, "a" * 64))
    entry = catalog.save_file(account_id, root, file_id, "report.pdf", "p5-save")
    discovery = WorkspaceDiscovery(database_url)
    assert discovery.search(account_id, name="report", content_type="application/pdf")["items"][0]["node_id"] == str(entry)
    assert discovery.search(account_id, name="absent")["items"] == []
    second = catalog.save_file(account_id, root, file_id, "copy.pdf", "p5-save-copy")
    assert second != entry
    assert discovery.retention(account_id, file_id)["references"]["active_entries"] == 2
    assert discovery.space(account_id)["bytes_with_active_entry"] == 7
    assert discovery.trace(account_id, entry)["saves"][0]["operation_id"] == "p5-save"
    run_id = UUID(CommandService(database_url).send_message(
        account_id, conversation, str(uuid4()), "read")['run_id'])
    assert discovery.search(account_id, run_id=run_id)["items"] == []
    ResourcePolicy(database_url).grant(account_id, "conversation", conversation,
                                       entry, ["list_metadata"], False)
    # A grant after Run creation cannot extend its fixed candidate set.
    assert discovery.search(account_id, run_id=run_id)["items"] == []
    next_conversation = UUID(CommandService(database_url).create_conversation(
        account_id, str(uuid4()))["conversation_id"])
    ResourcePolicy(database_url).grant(account_id, "conversation", next_conversation,
                                       entry, ["list_metadata"], False)
    next_run = UUID(CommandService(database_url).send_message(
        account_id, next_conversation, str(uuid4()), "read again")['run_id'])
    assert len(discovery.search(account_id, run_id=next_run)["items"]) == 1
    db.execute("INSERT INTO workspace_file_summaries(account_id,file_id,sha256,summary,source_run_id) "
               "VALUES (%s,%s,%s,'confidential brief',%s)",
               (account_id, file_id, "a" * 64, next_run))
    assert discovery.search(account_id, run_id=next_run, summary="confidential")["items"] == []
    assert discovery.search(account_id, run_id=next_run)["items"][0]["summary"] is None
    ResourcePolicy(database_url).grant(account_id, "conversation", next_conversation,
                                       entry, ["read_content"], False)
    summary_item = discovery.search(account_id, run_id=next_run,
                                    summary="confidential")["items"][0]
    assert summary_item["summary_sha256"] == "a" * 64

    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    assert discovery.search(other)["items"] == []
    assert discovery.search(account_id, run_id=next_run, name="secret")["items"] == []
