from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from application.chat.service import ChatCommandService
from application.context_assembly import ContextAssemblyService, ContextIsolationError
from application.context_builder import HarnessContextBuilder

pytestmark = pytest.mark.postgres


def _conversation(service: ChatCommandService, account_id: UUID) -> UUID:
    return UUID(service.create_conversation(account_id, str(uuid4()))["conversation_id"])


def _complete(service: ChatCommandService, account_id: UUID, run_id: UUID) -> None:
    assert service.start_run(account_id, run_id)
    assert service.complete_run(account_id, run_id, "completed reply")


def test_context_uses_only_frozen_visible_messages_of_its_conversation(
    db, account_id, database_url, worker_database_url
):
    commands = ChatCommandService(database_url)
    first, other = _conversation(commands, account_id), _conversation(commands, account_id)
    first_run = UUID(commands.send_message(account_id, first, str(uuid4()), "first user")["run_id"])
    _complete(ChatCommandService(worker_database_url), account_id, first_run)
    run_id = UUID(commands.send_message(account_id, first, str(uuid4()), "second user")["run_id"])
    commands.send_message(account_id, other, str(uuid4()), "other conversation")
    db.execute(
        "INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,sequence,"
        "client_request_id) VALUES (%s,%s,%s,'user','accepted','after watermark',5,%s)",
        (uuid4(), account_id, first, uuid4()),
    )

    base = ContextAssemblyService(database_url, HarnessContextBuilder()).load_base(account_id, run_id)

    assert [event.content.get("content", event.content.get("text")) for event in base.short_term_events] == [
        "first user", "completed reply", "second user"
    ]


def test_retry_context_reuses_the_original_watermark_without_duplicate_trigger(
    account_id, database_url, worker_database_url
):
    commands = ChatCommandService(database_url)
    conversation = _conversation(commands, account_id)
    source = UUID(commands.send_message(account_id, conversation, str(uuid4()), "retry me")["run_id"])
    ChatCommandService(worker_database_url).fail_run(account_id, source, "failed")
    retry = UUID(commands.retry_run(account_id, source, str(uuid4()))["run_id"])

    base = ContextAssemblyService(database_url, HarnessContextBuilder()).load_base(account_id, retry)

    assert base.context_message_seq == 1
    assert [event.content["content"] for event in base.short_term_events] == ["retry me"]


def test_context_refuses_cross_account_run_lookup(account_id, database_url):
    commands = ChatCommandService(database_url)
    conversation = _conversation(commands, account_id)
    run_id = UUID(commands.send_message(account_id, conversation, str(uuid4()), "private")["run_id"])

    with pytest.raises(ContextIsolationError):
        ContextAssemblyService(database_url, HarnessContextBuilder()).load_base(uuid4(), run_id)


def test_web_long_text_and_history_remain_raw_with_global_budget(
    db, account_id, database_url, worker_database_url,
):
    from common.token_counter import estimate_messages_tokens

    commands = ChatCommandService(database_url)
    conversation = _conversation(commands, account_id)
    history = 'Web文章🙂代码\n' * 2000
    first = UUID(commands.send_message(account_id, conversation, str(uuid4()), history)['run_id'])
    _complete(ChatCommandService(worker_database_url), account_id, first)
    text = '长篇需求中文Unicode🙂\nprint("raw")\n' * 2000
    run_id = UUID(commands.send_message(account_id, conversation, str(uuid4()), text)['run_id'])
    assert [r[0] for r in db.execute("SELECT content FROM messages WHERE role='user' ORDER BY sequence")] == [history, text]
    context = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    base = context.load_base(account_id, run_id)
    messages = context.compose(base, ())
    user_content = [m['content'] for m in messages if m['role'] == 'user']
    assert any(m.endswith(history) for m in user_content)
    assert user_content[-1].endswith(text)
    assert all(' ...' not in m for m in user_content)
    assert not any(m.startswith('[群聊背景') for m in user_content)
    assert estimate_messages_tokens(messages) <= 240000
    # The shared builder still applies the overall budget, never QQ clipping.
    builder = HarnessContextBuilder(system_prompt='rules')
    events = list(base.short_term_events)
    latest_only = builder.build(events[-1:], token_budget=240000, generation_headroom=16000)
    reduced = builder.build(events, token_budget=estimate_messages_tokens(latest_only) + 100,
                            generation_headroom=50)
    assert reduced[-1]['content'] == latest_only[-1]['content']
    assert not any(m['content'].endswith(history) for m in reduced)
