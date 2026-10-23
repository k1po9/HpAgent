"""Hermetic S7-1 safety envelope and negative evidence checks; no Provider calls."""
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('s7_cli', ROOT / 'scripts/acceptance/s7_1.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
from s7.core import (
    AUTHORIZATION_ID,
    EXTENDED_AUTHORIZATION_ID,
    Ledger,
    Rejected,
    abandon_unknown,
    request_bound,
    upgrade_authorization,
    validate,
)
from s7.evidence import budget_rejection_before_dispatch, judge, saved_html


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with patch.object(cli, 'CAMPAIGN', self.root / 'campaign'), patch.object(cli, 'image', return_value='sha256:' + 'a' * 64), patch.object(cli, 'fingerprint', return_value={k: 'fixture' for k in ('head', 'tree_hash', 'migration_hash', 'runtime_hash')}):
            self.m = cli.create_manifest(self.root / 'batch', 11)
        self.ledger = Ledger(self.root / 'ledger.sqlite', self.m)

    def test_mvp_upgrade_is_explicit_idempotent_and_preserves_every_history_table(self):
        from s7.core import MVP_AUTHORIZATION_ID
        self.ledger.reserve('history', 'M1-1', 1000, {'account': 'original'})
        self.ledger.uncertain('history')
        self.ledger.consume('M3-1', {'operation': 'original'}, 'one-shot')
        self.ledger.halt('original failure')
        upgrade_authorization(self.ledger.path, AUTHORIZATION_ID, 1000000, 600000)
        upgrade_authorization(self.ledger.path, EXTENDED_AUTHORIZATION_ID, 1600000, 3200000)
        with self.ledger.connect() as db:
            before = {t: db.execute('SELECT * FROM ' + t).fetchall() for t in
                      ('sends', 'batch', 'events', 'faults', 'halted')}
        self.assertTrue(upgrade_authorization(self.ledger.path, MVP_AUTHORIZATION_ID, 4800000, 9200000))
        self.assertFalse(upgrade_authorization(self.ledger.path, MVP_AUTHORIZATION_ID, 4800000, 9200000))
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT * FROM authorization').fetchone(), (1, 11, 14000000))
            for t, rows in before.items():
                self.assertEqual(rows, db.execute('SELECT * FROM ' + t).fetchall())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM authorization_upgrades').fetchone()[0], 3)
        # Old frozen batches remain readable and cannot send after upgrade.
        historical = Ledger(self.ledger.path, self.m)
        self.assertEqual(historical.summary()['uncertain_requests'], 1)
        with self.assertRaises(Rejected):
            historical.reserve('forbidden', 'M1-1', 100, {})

    def test_mvp_upgrade_rejects_wrong_old_limit_and_amount(self):
        from s7.core import MVP_AUTHORIZATION_ID
        with self.assertRaises(Rejected):
            upgrade_authorization(self.ledger.path, MVP_AUTHORIZATION_ID, 4800000, 9200000)
        with self.assertRaises(Rejected):
            upgrade_authorization(self.ledger.path, MVP_AUTHORIZATION_ID, 1000000, 13000000)
        self.assertEqual(self.ledger.summary()['cumulative_tokens'], 11)

    def test_mvp_manifest_requires_normal_discovery_and_no_fault_matrix(self):
        from s7.mvp import configure_manifest
        m = copy.deepcopy(self.m)
        configure_manifest(m, lambda _: 'sha256:' + 'a' * 64)
        m['limits']['batch_tokens'] = 14000000
        self.assertEqual(validate(m)['scenarios'], {'research_1': 1, 'revision': 1, 'research_2': 1})
        for key, value in [('discovery', 'curated'), ('faults', ['M3'])]:
            bad = copy.deepcopy(m)
            bad[key] = value
            with self.assertRaises(Rejected):
                validate(bad)

    def test_mvp_outbound_proxy_is_frozen_without_credentials(self):
        from s7.mvp import configure_manifest
        m = copy.deepcopy(self.m)
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://127.0.0.1:1234'}):
            configure_manifest(m, lambda _: 'sha256:' + 'a' * 64)
        self.assertEqual(m['resources']['outbound_proxy'], 'http://127.0.0.1:1234')
        self.assertIn('all://: ["http://127.0.0.1:1234"]', m['search_settings'])
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://secret:token@localhost:1234'}):
            with self.assertRaises(ValueError):
                configure_manifest(copy.deepcopy(self.m), lambda _: 'sha256:' + 'a' * 64)

    def test_proxy_routes_approved_connect_through_upstream_and_denies_other_hosts(self):
        import io
        import json
        import socket
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from s7 import proxy
        m = copy.deepcopy(self.m)
        m['resources']['outbound_proxy'] = 'http://proxy:1234'
        handler = proxy.Handler.__new__(proxy.Handler)
        handler.connection = MagicMock()
        upstream = MagicMock()
        upstream.__enter__.return_value = upstream
        upstream.recv.side_effect = [bytes([b]) for b in b'HTTP/1.1 200 Connection Established\r\n\r\n']
        with patch.object(proxy, 'Path', return_value=SimpleNamespace(read_text=lambda: json.dumps(m))), patch.object(proxy.socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('8.8.8.8', 443))]), patch.object(proxy.socket, 'create_connection', return_value=upstream) as connect, patch.object(proxy, 'relay') as relay:
            handler.rfile = io.BytesIO(b'CONNECT docs.python.org:443 HTTP/1.1\r\n\r\n')
            handler.wfile = io.BytesIO()
            handler.handle()
            connect.assert_called_once_with(('proxy', 1234), timeout=10)
            self.assertIn(b'200 Connection Established', handler.wfile.getvalue())
            relay.assert_called_once()
            connect.reset_mock()
            handler.rfile = io.BytesIO(b'CONNECT forbidden.example:443 HTTP/1.1\r\n\r\n')
            handler.wfile = io.BytesIO()
            handler.handle()
            self.assertIn(b'403 Forbidden', handler.wfile.getvalue())
            connect.assert_not_called()

    def tearDown(self):
        self.temp.cleanup()

    def test_freeze_and_offline_preflight_do_not_read_secrets_or_send(self):
        with patch.object(cli, 'exact_model_fields') as secret, patch.object(cli, 'fingerprint', return_value=self.m['code']):
            cli.preflight(self.root / 'batch')
            secret.assert_not_called()

    def test_incomplete_and_unauthorized_manifests_fail_closed(self):
        for key in ('code', 'model', 'limits', 'resources', 'browser_assertion', 'clarification', 'sources'):
            with self.subTest(key=key):
                m = copy.deepcopy(self.m)
                del m[key]
                with self.assertRaises(Rejected):
                    validate(m)
        for field, value in [('batch_tokens', 1000001), ('prior_tokens', 0), ('request_seconds', float('inf')), ('requests', True)]:
            m = copy.deepcopy(self.m)
            m['limits'][field] = value
            with self.assertRaises(Rejected):
                validate(m)
        for key, value in [('fallbacks', ['other']), ('auxiliary_models', ['embedding']), ('stream', True), ('id', 'other-model')]:
            m = copy.deepcopy(self.m)
            m['model'][key] = value
            with self.assertRaises(Rejected):
                validate(m)

    def test_restarts_accounts_and_dates_cannot_reset_cumulative_budget(self):
        self.ledger.reserve('one', 'M1-1', 40000, {'account': 'a', 'day': 'one'})
        self.ledger.settle('one', {'usage_source': 'provider', 'total_tokens': 100})
        restarted = Ledger(self.root / 'ledger.sqlite', self.m)
        restarted.reserve('two', 'M1-2', 40000, {'account': 'b', 'day': 'two'})
        self.assertEqual(restarted.summary()['cumulative_tokens'], 40111)
        with self.assertRaises(Rejected):
            restarted.reserve('oversized', 'M1-3', 1000000, {})
        with self.assertRaises(Rejected):
            restarted.reserve('one', 'M1-3', 10, {})

    def test_live_concurrent_requests_hold_bounds_without_becoming_unknown(self):
        from concurrent.futures import ThreadPoolExecutor
        def reserve(index):
            try:
                self.ledger.reserve(f'parallel-{index}', f'M1-{index}', 131072, {'account': str(index)})
                return True
            except Rejected:
                return False
        with ThreadPoolExecutor(10) as pool:
            accepted = list(pool.map(reserve, range(10)))
        self.assertEqual(sum(accepted), 7)
        summary = self.ledger.summary()
        self.assertEqual(summary['inflight_requests'], 7)
        self.assertEqual(summary['uncertain_requests'], 0)
        self.assertEqual(summary['held_tokens'], 7 * 131072)
        self.assertLessEqual(summary['cumulative_tokens'], self.m['limits']['batch_tokens'])
        self.ledger.uncertain('parallel-' + str(accepted.index(True)))
        with self.assertRaisesRegex(Rejected, 'uncertain'):
            self.ledger.reserve('new', 'M3-1', 1, {})

    def test_missing_usage_holds_bound_and_blocks_all_further_sends(self):
        self.ledger.reserve('one', 'M1-1', 10000, {})
        with self.assertRaises(Rejected):
            self.ledger.settle('one', {'usage_source': 'estimated', 'total_tokens': 5})
        self.assertEqual(self.ledger.summary()['cumulative_tokens'], 10011)
        with self.assertRaises(Rejected):
            self.ledger.reserve('other-account', 'M3-1', 5, {})

    def test_upper_bound_breach_is_not_lost_from_accounting(self):
        self.ledger.reserve('one', 'M1-1', 10, {})
        with self.assertRaises(Rejected):
            self.ledger.settle('one', {'usage_source': 'provider', 'total_tokens': 15})
        self.assertEqual(self.ledger.summary()['cumulative_tokens'], 26)
        self.assertEqual(self.ledger.summary()['uncertain_requests'], 1)

    def test_one_shot_fault_survives_restart_and_cannot_hit_another_invocation(self):
        self.assertTrue(self.ledger.consume('M3-1', {'invocation': 'original'}, 'invalid_html'))
        restarted = Ledger(self.root / 'ledger.sqlite', self.m)
        self.assertFalse(restarted.consume('M3-1', {'invocation': 'next'}, 'invalid_html'))
        self.assertTrue(restarted.consume('M3-2', {'invocation': 'next'}, 'invalid_html'))

    def test_ledger_cannot_be_reused_after_config_change(self):
        m = copy.deepcopy(self.m)
        m['limits']['scenario_requests'] -= 1
        with self.assertRaises(Rejected):
            Ledger(self.root / 'ledger.sqlite', m)

    def test_explicit_upgrade_is_atomic_idempotent_and_preserves_history(self):
        from concurrent.futures import ThreadPoolExecutor
        path = self.root / 'ledger.sqlite'
        self.ledger.reserve('history', 'M1-1', 1000, {})
        self.ledger.settle('history', {'usage_source': 'provider', 'total_tokens': 500})
        self.ledger.halt('original failure')
        self.ledger.consume('M3-1', {'original': True}, 'fault')
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: upgrade_authorization(path, AUTHORIZATION_ID, 1_000_000, 600_000), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.ledger.summary()['cumulative_tokens'], 511)
        historical = Ledger(path, self.m)
        with self.assertRaises(Rejected):
            historical.reserve('old', 'M1-1', 1, {})
        with historical.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM authorization_upgrades').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM faults').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM halted').fetchone()[0], 1)
        m = copy.deepcopy(self.m)
        m['batch_id'] = 's7-cccccccccccc'
        m['limits']['batch_tokens'] = 1_600_000
        upgraded = Ledger(path, m)
        upgraded.reserve('new-account-day', 'M1-1', 1000, {'account': 'new', 'day': 'next'})
        self.assertEqual(upgraded.summary()['cumulative_tokens'], 1511)
        for args in [('arbitrary', 1_000_000, 600_000), (AUTHORIZATION_ID, 999_999, 600_000),
                     (AUTHORIZATION_ID, 1_000_000, 600_001)]:
            with self.assertRaises(Rejected):
                upgrade_authorization(path, *args)
        with upgraded.connect() as db:
            db.execute('DELETE FROM authorization_upgrades')
        with self.assertRaisesRegex(Rejected, 'old limit'):
            upgrade_authorization(path, AUTHORIZATION_ID, 1_000_000, 600_000)

    def test_proxy_keeps_response_after_legacy_tunnel_deadline(self):
        from unittest.mock import Mock

        from s7 import proxy

        client, upstream = Mock(), Mock()
        upstream.recv.return_value = b'provider response bytes'
        # The provider first responds after the old 120-second tunnel expiry.
        with patch.object(proxy.time, 'monotonic', side_effect=[0, 125, 240, 271]), patch.object(proxy.select, 'select', return_value=([upstream], [], [])):
            proxy.relay(client, upstream, self.m['resources']['egress_tunnel_seconds'])
        self.assertEqual(client.sendall.call_count, 2)
        for seconds in (120, 240, 301):
            m = copy.deepcopy(self.m)
            m['resources']['egress_tunnel_seconds'] = seconds
            with self.assertRaises(Rejected):
                validate(m)

    def test_budget_rejection_uses_public_failure_dto_and_database_fact(self):
        terminal = {'run_id': 'run', 'status': 'failed', 'failure': {'code': 'account_daily_model_budget_exhausted'}}
        facts = {'runs': [{'run_id': 'run', 'failure_code': 'account_daily_model_budget_exhausted'}], 'model_dispatches': [], 'works': []}
        self.assertTrue(budget_rejection_before_dispatch(terminal, facts, []))
        self.assertFalse(budget_rejection_before_dispatch(terminal, facts, ['reserved-send']))
        self.assertFalse(budget_rejection_before_dispatch(terminal, {**facts, 'runs': []}, []))
        self.assertFalse(budget_rejection_before_dispatch({**terminal, 'failure': {'code': 'other'}}, facts, []))
        self.ledger.halt('independent quality failure')
        self.assertEqual(self.ledger.halt_reason(), 'independent quality failure')
        restarted = Ledger(self.root / 'ledger.sqlite', self.m)
        self.assertEqual(restarted.halt_reason(), 'independent quality failure')

    def test_artifact_timeout_is_frozen_below_activity_bound(self):
        self.assertEqual(self.m['model']['artifact_read_seconds'], 240)
        self.assertEqual(self.m['limits']['request_seconds'], 240)
        for value in (0, True, 301, 241):
            m = copy.deepcopy(self.m)
            m['model']['artifact_read_seconds'] = value
            with self.assertRaises(Rejected):
                validate(m)

    def test_second_upgrade_preserves_unknown_and_explicit_abandonment(self):
        path = self.root / 'ledger.sqlite'
        self.ledger.reserve('01a15f22-9cee-7ab0-9d82-ea85b81b675b', 'M1-1', 15664, {})
        self.ledger.uncertain('01a15f22-9cee-7ab0-9d82-ea85b81b675b')
        self.ledger.halt('unknown receipt')
        upgrade_authorization(path, AUTHORIZATION_ID, 1_000_000, 600_000)
        upgrade_authorization(path, EXTENDED_AUTHORIZATION_ID, 1_600_000, 3_200_000)
        self.assertFalse(upgrade_authorization(path, AUTHORIZATION_ID, 1_000_000, 600_000))
        self.assertFalse(upgrade_authorization(path, EXTENDED_AUTHORIZATION_ID, 1_600_000, 3_200_000))
        m = copy.deepcopy(self.m)
        m['batch_id'] = 's7-dddddddddddd'
        m['limits']['batch_tokens'] = 4_800_000
        ledger = Ledger(path, m)
        with self.assertRaisesRegex(Rejected, 'uncertain'):
            ledger.reserve('fresh', 'M1-1', 100, {})
        with self.assertRaises(Rejected):
            abandon_unknown(path, '01a15f22-9cee-7ab0-9d82-ea85b81b675b', 'unapproved')
        self.assertTrue(abandon_unknown(path, '01a15f22-9cee-7ab0-9d82-ea85b81b675b', 's7-1-20261021-user-abandon-held'))
        self.assertFalse(abandon_unknown(path, '01a15f22-9cee-7ab0-9d82-ea85b81b675b', 's7-1-20261021-user-abandon-held'))
        ledger.reserve('fresh', 'M1-1', 100, {})
        with self.assertRaisesRegex(Rejected, 'resent'):
            ledger.reserve('01a15f22-9cee-7ab0-9d82-ea85b81b675b', 'M1-1', 1, {})
        self.assertEqual(ledger.summary()['held_tokens'], 15764)
        self.assertEqual(ledger.summary()['abandoned_held_requests'], 1)
        self.assertEqual(ledger.summary()['cumulative_tokens'], 15775)

    def test_whole_tool_schema_and_growing_sequence_stay_inside_envelope(self):
        body = {'model': cli.MODEL, 'max_tokens': 8192, 'messages': [{'role': 'user', 'content': 'ordinary chat'}]}
        small = request_bound(body)
        body['tools'] = [{'type': 'function', 'function': {'name': f'tool{i}', 'description': 'schema ' * 100,
                         'parameters': {'type': 'object', 'properties': {'objective': {'type': 'string'}}}}} for i in range(17)]
        assert request_bound(body) > small
        used = 0
        for i, content in enumerate(['research evidence ' * 500, 'report ' * 1000, 'HTML receipt ' * 100, 'save receipt']):
            body['messages'].append({'role': 'tool', 'content': content, 'tool_call_id': str(i)})
            bound = request_bound(body)
            assert used + bound <= self.m['limits']['scenario_tokens']
            self.ledger.reserve(f'sequence-{i}', 'M1-1', bound, {'phase': content[:8]})
            self.ledger.settle(f'sequence-{i}', {'usage_source': 'provider', 'total_tokens': 5000})
            used += 5000
        assert self.ledger.summary()['measured_tokens'] == 20000
        oversized = {'messages': body['messages'], 'tools': body['tools'] * 10, 'max_tokens': 4096}
        with self.assertRaises(Rejected):
            self.ledger.reserve('rejected-before-dispatch', 'M1-1', request_bound(oversized), {})
        assert self.ledger.summary()['requests'] == 4

    def test_revision_carries_full_old_html_in_child_and_parent_envelopes(self):
        old_html = '<html><body>' + '<article>TaskGroup and Lock</article>' * 500 + '</body></html>'
        child = {'model': cli.MODEL, 'max_tokens': 8192, 'messages': [{'role': 'user', 'content': old_html + '\nPrevious HTML:\n' + old_html + '\n' + 'x' * 4000}]}
        bound = request_bound(child)
        self.assertLessEqual(bound, self.m['limits']['request_tokens'])
        self.assertLessEqual(130262 + bound, self.m['limits']['scenario_tokens'])
        self.assertEqual(self.m['effective_governance']['account_day_tokens'], 800000)

    def test_frozen_work_run_profile_is_applied_before_budget_snapshot(self):
        import json

        from s7.composition import configure_governance

        from persistence.repositories import RunBudgetRepository
        from resources import work_budget
        captured = []
        def create(*args):
            captured.append(args)
        with patch.object(RunBudgetRepository, 'create_snapshot', create), patch.object(work_budget, 'DEFAULT_LIMITS', dict(work_budget.DEFAULT_LIMITS)):
            configure_governance(self.m)
            repo = RunBudgetRepository()
            limits = json.dumps({'model_calls': 40, 'model_total_tokens': 100000})
            repo.create_snapshot(None, None, None, None, 'work-foundation-v1', 'enforce', limits, 0)
            frozen = json.loads(captured[-1][-2])
            self.assertEqual(frozen['model_total_tokens'], self.m['limits']['run_tokens'])
            self.assertEqual(frozen['model_input_tokens'] + frozen['model_output_tokens'], frozen['model_total_tokens'])
            self.assertEqual(captured[-1][-4], 'work-foundation-v1-s7-1')
            repo.create_snapshot(None, None, None, None, 'chat-v1', 'enforce', limits, 0)
            self.assertEqual(json.loads(captured[-1][-2])['model_total_tokens'], 100000)
            self.assertEqual(work_budget.DEFAULT_LIMITS['model_total_tokens'], self.m['limits']['work_tokens'])
        self.assertGreater(self.m['limits']['scenario_tokens'], self.m['limits']['work_tokens'])

    def test_no_evidence_or_self_report_can_pass(self):
        assertions = judge({}, work='w', source_message='m', scenario='M1', provider_events=[], browser={'passed': True})
        self.assertFalse(all(a['passed'] for a in assertions))
        self.assertFalse(next(a['passed'] for a in assertions if a['id'] == 'real_parent_decision'))
        self.assertFalse(next(a['passed'] for a in assertions if a['id'] == 'work_completed'))

    def test_exact_secret_reader_rejects_shell_and_duplicate_values_without_echoing(self):
        p = self.root / 'private.env'
        p.write_text('UNRELATED=$(must-not-run)\nSILICONFLOW_FLAGSHIP_MODEL="' + cli.MODEL + '"\nSILICONFLOW_BASE_URL=' + cli.BASE_URL + '\nSILICONFLOW_API_KEY=secret-fixture\n')
        self.assertEqual(set(cli.exact_model_fields(p)), {'SILICONFLOW_FLAGSHIP_MODEL', 'SILICONFLOW_BASE_URL', 'SILICONFLOW_API_KEY'})
        p.write_text(p.read_text() + 'SILICONFLOW_API_KEY=duplicate-fixture\n')
        with self.assertRaisesRegex(Rejected, 'duplicate'):
            cli.exact_model_fields(p)


    def test_new_batch_keeps_campaign_usage_but_has_separate_sample_identity(self):
        self.ledger.reserve('first-batch-call', 'M1-1', 1000, {})
        self.ledger.settle('first-batch-call', {'usage_source': 'provider', 'total_tokens': 500})
        m = copy.deepcopy(self.m)
        m['batch_id'] = 's7-aaaaaaaaaaaa'
        other = Ledger(self.root / 'ledger.sqlite', m)
        other.reserve('next-batch-call', 'M1-1', 1000, {})
        self.assertEqual(other.summary()['cumulative_tokens'], 1511)
        self.assertEqual(other.summary()['cumulative_requests'], 3)
        self.assertEqual([row[0] for row in other.batch_sends('M1-1')], ['next-batch-call'])
        self.assertEqual(other.batch_sends('M3-1'), [])
        self.assertEqual([row[0] for row in self.ledger.batch_sends()], ['first-batch-call'])
        with other.connect() as db:
            self.assertEqual(db.execute('SELECT count(DISTINCT batch_id) FROM sends').fetchone()[0], 2)


    def test_stopped_batch_cannot_send_but_a_new_batch_keeps_the_same_campaign_charge(self):
        self.ledger.reserve('first', 'M1-1', 50, {})
        self.ledger.settle('first', {'usage_source': 'provider', 'total_tokens': 20})
        self.ledger.halt('driver failed')
        with self.assertRaisesRegex(Rejected, 'batch stopped'):
            self.ledger.reserve('forbidden', 'M1-1', 50, {})
        m = copy.deepcopy(self.m)
        m['batch_id'] = 's7-bbbbbbbbbbbb'
        next_batch = Ledger(self.root / 'ledger.sqlite', m)
        next_batch.reserve('allowed-new-batch', 'M1-1', 50, {})
        self.assertEqual(next_batch.summary()['cumulative_tokens'], 81)


    def test_saved_html_requires_the_final_exact_subject_bytes_and_destination(self):
        sha = 'a' * 64
        facts = {
            'runs': [{'run_id': 'run', 'work_id': 'work', 'status': 'succeeded', 'result_json': {'evidence': [{'type': 'workspace_saved', 'ref': 'evidence'}]}}],
            'capability_invocations': [{'invocation_id': 'html', 'capability_key': 'html', 'run_id': 'run'}],
            'capability_results': [{'invocation_id': 'html', 'payload': {'outcome': 'succeeded'}}],
            'capability_result_refs': [{'ref_id': 'ref', 'invocation_id': 'html', 'type': 'artifact_version', 'artifact_version_id': 'version', 'sha256': sha}],
            'artifact_versions': [{'artifact_version_id': 'version', 'status': 'completed', 'file_id': 'file'}],
            'workspace_nodes': [{'node_id': 'node', 'kind': 'file', 'deleted_at': None, 'file_id': 'file', 'parent_id': 'directory'}],
            'stored_files': [{'file_id': 'file', 'status': 'ready', 'sha256': sha}],
            'capability_acceptance_evidence': [{'evidence_id': 'evidence', 'predicate': {'kind': 'workspace_saved', 'subject_ref': 'ref', 'target_id': 'node', 'operation_id': 'save'}}],
            'capability_result_checks': [{'subject_ref': 'ref', 'verifier_key': 'html.basic', 'verifier_version': '1', 'verdict': 'passed'}],
            'workspace_save_operations': [{'source_run_id': 'run', 'source_kind': 'work_auto', 'node_id': 'node', 'operation_id': 'save'}],
        }
        self.assertEqual(len(saved_html(facts, 'work', 'directory')), 1)
        self.assertFalse(saved_html(facts, 'work', 'other-directory'))
        facts['stored_files'][0]['sha256'] = 'b' * 64
        self.assertFalse(saved_html(facts, 'work', 'directory'))
        facts['stored_files'][0]['sha256'] = sha
        facts['runs'][0]['result_json']['evidence'] = []
        self.assertFalse(saved_html(facts, 'work', 'directory'))


if __name__ == '__main__':
    unittest.main()
