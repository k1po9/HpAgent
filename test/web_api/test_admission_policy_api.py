"""The HTTP process admits the same effective YAML policy as the Worker."""
from uuid import UUID, uuid4

import pytest
from test_phase_b_api import command_headers, create_conversation

pytestmark = pytest.mark.postgres


def test_http_admission_uses_standard_yaml_and_command_replay_keeps_original_policy(logged_client, db):
    from common.agent_policy import AgentPolicy
    client, csrf, account = logged_client
    response = create_conversation(client, csrf)
    cid = response.json()['conversation']['conversation_id']
    key = str(uuid4())
    sent = client.post(f'/api/v1/conversations/{cid}/messages', json={'content': 'policy check'},
                       headers=command_headers(csrf, key))
    assert sent.status_code == 202
    run = UUID(sent.json()['run']['run_id'])
    snapshot = db.execute('SELECT input_snapshot FROM runs WHERE account_id=%s AND run_id=%s', (account, run)).fetchone()[0]
    assert snapshot['agent_policy']['max_tool_turns'] == 5
    client.app.state.commands.agent_policy = AgentPolicy(max_tool_turns=17)
    replay = client.post(f'/api/v1/conversations/{cid}/messages', json={'content': 'policy check'},
                         headers=command_headers(csrf, key))
    assert replay.status_code == 202 and replay.json() == sent.json()
    assert db.execute('SELECT input_snapshot FROM runs WHERE run_id=%s', (run,)).fetchone()[0] == snapshot
