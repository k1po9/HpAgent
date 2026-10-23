"""Offline S7-1 contracts. No application imports, credentials or network access."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from urllib.parse import urlsplit

SCENARIOS = {**{f'M{i}': 3 for i in (1, 2, 3)}, **{f'M{i}': 1 for i in range(4, 8)}}
MODEL = 'deepseek-ai/DeepSeek-V4-Flash'
BASE_URL = 'https://api.siliconflow.cn/v1'
AUTHORIZATION_ID = 's7-1-20261021-add-600000'
EXTENDED_AUTHORIZATION_ID = 's7-1-20261021-total-4800000'
TOKEN_LIMIT = 4_800_000
MVP_AUTHORIZATION_ID = 'mvp-20261022-total-14000000'
MVP_TOKEN_LIMIT = 14_000_000
MVP_SCENARIOS = {'research_1': 1, 'revision': 1, 'research_2': 1}
UPGRADES = {AUTHORIZATION_ID: (1_000_000, 600_000),
            EXTENDED_AUTHORIZATION_ID: (1_600_000, 3_200_000),
            MVP_AUTHORIZATION_ID: (4_800_000, 9_200_000)}


class Rejected(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def request_bound(body):
    """Whole serialized provider body, including tools/history, plus output/framing."""
    maximum = positive(body.get('max_tokens'), 'output allocation')
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return len(encoded) * 2 + maximum + 4096


def positive(value, label):
    if type(value) is not int or value <= 0:
        raise Rejected(f'{label}: positive integer required')
    return value


def validate(m):
    """Fail closed before preparation; the manifest contains no credentials/DSNs."""
    try:
        if m['schema_version'] != 1 or not re.fullmatch(r's7-[a-f0-9]{12}', m['batch_id']):
            raise Rejected('invalid version or batch identity')
        mvp = m.get('profile') == 'research-html-mvp'
        if m.get('profile') not in (None, 'research-html-mvp'):
            raise Rejected('unsupported acceptance profile')
        if m['strategy'] != 'react' or m['scenarios'] != (MVP_SCENARIOS if mvp else SCENARIOS):
            raise Rejected('the frozen ReAct matrix must contain 13 samples')
        model = m['model']
        if (model['provider'], model['id'], model['base_url']) != ('siliconflow', MODEL, BASE_URL):
            raise Rejected('model differs from the confirmed authorization; refreeze required')
        if model['fallbacks'] or model['auxiliary_models'] or model['stream']:
            raise Rejected('fallback/auxiliary models/stream not authorized for this batch')
        if model['routes'] != ['chat', 'fast', 'reasoning']:
            raise Rejected('all text routes must be explicit')
        positive(model['max_output_tokens'], 'output tokens')
        limits = m['limits']
        for key in ('batch_tokens', 'prior_tokens', 'request_tokens', 'scenario_tokens',
                    'run_tokens', 'work_tokens', 'requests', 'scenario_requests',
                    'request_seconds', 'scenario_seconds', 'batch_seconds', 'output_bytes'):
            positive(limits[key], key)
        if type(limits.get('prior_requests', 1)) is not int or limits.get('prior_requests', 1) < 1:
            raise Rejected('known prior probe request must be included')
        if limits['batch_tokens'] not in (1_000_000, 1_600_000, TOKEN_LIMIT, MVP_TOKEN_LIMIT) or limits['prior_tokens'] < 11:
            raise Rejected('cumulative authorized token boundary violated')
        if (mvp and limits['batch_tokens'] != MVP_TOKEN_LIMIT) or (not mvp and limits['batch_tokens'] == MVP_TOKEN_LIMIT):
            raise Rejected('cumulative limit does not match acceptance profile')
        artifact_timeout = model.get('artifact_read_seconds', 90)
        positive(artifact_timeout, 'artifact read seconds')
        if artifact_timeout > min(300, limits['request_seconds']):
            raise Rejected('artifact timeout exceeds frozen request or Activity bounds')
        if limits['prior_tokens'] >= limits['batch_tokens']:
            raise Rejected('no remaining tokens')
        if not (limits['request_tokens'] <= limits['scenario_tokens'] <= limits['batch_tokens']
                and limits['run_tokens'] <= limits['work_tokens'] <= limits['batch_tokens']):
            raise Rejected('invalid token bounds')
        if limits['run_tokens'] > limits['work_tokens'] or limits['scenario_requests'] > limits['requests']:
            raise Rejected('invalid run/request bounds')
        if not (limits['request_seconds'] < limits['scenario_seconds'] < limits['batch_seconds']):
            raise Rejected('timeouts must be finite and ordered')
        if m.get('dispatch_guard_version') == 1:
            governance = m['effective_governance']
            if (limits['run_tokens'] != governance['generic_work_run_tokens'] or
                    limits['work_tokens'] != governance['work_tokens']):
                raise Rejected('manifest governance differs from actual product limits')
        if m['faults'] != ([] if mvp else ['M3', 'M4', 'M5', 'M6']) or m['budget_mode'] != 'enforce':
            raise Rejected('fault allowlist / enforced budget required')
        disabled = {'qq', 'email', 'mcp', 'shell', 'git', 'publishing', 'memory', 'embedding', 'rerank', 'fake_executor'}
        if set(m['disabled']) != disabled:
            raise Rejected('side effect exclusions are incomplete')
        sources = m['sources']
        if not 2 <= len(sources) <= (6 if mvp else 3):
            raise Rejected('two or three frozen sources required')
        hosts = {urlsplit(s['url']).hostname for s in sources}
        for s in sources:
            u = urlsplit(s['url'])
            if u.scheme != 'https' or not u.hostname or u.username or u.port not in (None, 443):
                raise Rejected('only public HTTPS sources are allowed')
            if not s['facts'] or not s['locator']:
                raise Rejected('independent facts and source locators required')
        if mvp and (m['discovery'] != 'searxng' or m['effective_governance']['source_discovery'] != 'searxng'):
            raise Rejected('MVP requires normal production discovery')
        if set(m['egress_hosts']) != hosts | {'api.siliconflow.cn'}:
            raise Rejected('egress must exactly match approved source and provider hosts')
        r = m['resources']
        tunnel_seconds = positive(r.get('egress_tunnel_seconds', 120), 'egress tunnel seconds')
        if 'egress_tunnel_seconds' in r and not limits['request_seconds'] < tunnel_seconds <= 300:
            raise Rejected('egress tunnel must outlive requests within a finite Activity bound')
        if not r['internal_network'] or r['namespace'] != 'default':
            raise Rejected('dedicated internal network and Temporal service required')
        if len(set(r['ports'].values())) != len(r['ports']):
            raise Rejected('duplicate ports')
        for port in r['ports'].values():
            if type(port) is not int or not 1024 <= port <= 65535:
                raise Rejected('invalid local port')
        for key in ('python_image', 'postgres_image', 'redis_image', 'temporal_image', *(['search_image'] if mvp else [])):
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', r[key]):
                raise Rejected('immutable local image identity required')
        if not m['prompt'] or not m['clarification'] or not m['browser_assertion']:
            raise Rejected('frozen natural language intent and browser assertion required')
        for key in ('head', 'tree_hash', 'migration_hash', 'runtime_hash'):
            if not m['code'][key]:
                raise Rejected('code/dirty tree/migrations/runtime must be frozen')
    except (KeyError, TypeError, AttributeError) as exc:
        raise Rejected('incomplete manifest') from exc
    return m


def upgrade_authorization(path, authorization_id, expected_old, additional):
    """Explicit offline CAS; never inferred from a manifest or ledger constructor."""
    if UPGRADES.get(authorization_id) != (expected_old, additional):
        raise Rejected('only the confirmed cumulative authorization upgrade is allowed')
    new_limit = expected_old + additional
    with sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS authorization_upgrades ('
                   'identity TEXT PRIMARY KEY, old_limit INTEGER, new_limit INTEGER, at REAL)')
        replay = db.execute('SELECT old_limit,new_limit FROM authorization_upgrades WHERE identity=?',
                            (authorization_id,)).fetchone()
        current = db.execute('SELECT token_limit FROM authorization WHERE singleton=1').fetchone()
        if replay:
            if replay != (expected_old, new_limit) or not current or current[0] < new_limit:
                raise Rejected('authorization replay mismatch')
            return False
        if current != (expected_old,):
            raise Rejected('authorization old limit mismatch')
        db.execute('INSERT INTO authorization_upgrades VALUES (?,?,?,?)',
                   (authorization_id, expected_old, new_limit, time.time()))
        changed = db.execute('UPDATE authorization SET token_limit=? WHERE singleton=1 AND token_limit=?',
                             (new_limit, expected_old)).rowcount
        if changed != 1:
            raise Rejected('authorization concurrent update')
    return True


def abandon_unknown(path, identity, decision_id):
    """Explicit human disposition keeps the full charge and permanently forbids resend."""
    approved = {
        's7-1-20261021-user-abandon-held': ('01a15f22-9cee-7ab0-9d82-ea85b81b675b', 15664),
        's7-1-20261022-user-abandon-held': ('01a15f34-7129-7de1-baed-926c72418728', 21178),
        's7-1-20261022-user-abandon-held-proxy': ('01a15f3e-6406-75b6-bad2-44bfbedc427f', 20112),
    }
    if decision_id not in approved or approved[decision_id][0] != identity:
        raise Rejected('unknown manual disposition identity')
    with sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state,actual,bound,batch_id FROM sends WHERE id=?', (identity,)).fetchone()
        if not row or row[0] not in ('uncertain', 'abandoned_held') or row[1] is not None or row[2] != approved[decision_id][1]:
            raise Rejected('disposition requires an unmeasured uncertain send')
        if not db.execute('SELECT 1 FROM halted WHERE batch_id=?', (row[3],)).fetchone():
            raise Rejected('original batch must be halted')
        if row[0] == 'abandoned_held':
            return False
        db.execute('UPDATE sends SET state="abandoned_held" WHERE id=?', (identity,))
        db.execute('INSERT INTO events VALUES (?,?,?)',
                   (time.time(), 'manual_abandon_held', json.dumps(dict(identity=identity, decision_id=decision_id, held_tokens=row[2], resend=False))))
    return True


class Ledger:
    """External safety envelope, separate from the business budget ledger.

    All unknown sends hold the full conservative allocation and stop new sends.
    SQLite transactions serialize across Worker restarts and test accounts/days.
    """
    def __init__(self, path, manifest):
        self.path, self.m = str(path), validate(manifest)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS batch(identity TEXT PRIMARY KEY, manifest_hash TEXT, started REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS sends(id TEXT PRIMARY KEY, sample TEXT, bound INTEGER, actual INTEGER, state TEXT, correlation TEXT)')
            columns = {row[1] for row in db.execute('PRAGMA table_info(sends)')}
            if 'batch_id' not in columns:
                db.execute('ALTER TABLE sends ADD COLUMN batch_id TEXT')
            db.execute('CREATE TABLE IF NOT EXISTS faults(id TEXT PRIMARY KEY, sample TEXT, target TEXT, state TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS halted(batch_id TEXT PRIMARY KEY, reason TEXT, at REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS events(at REAL, kind TEXT, body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS authorization (singleton INTEGER PRIMARY KEY CHECK(singleton=1), prior_tokens INTEGER, token_limit INTEGER)')
            frozen = db.execute('SELECT prior_tokens,token_limit FROM authorization WHERE singleton=1').fetchone()
            expected = (self.m['limits']['prior_tokens'], self.m['limits']['batch_tokens'])
            row = db.execute('SELECT identity,manifest_hash FROM batch WHERE identity=?', (self.m['batch_id'],)).fetchone()
            historical = row == (self.m['batch_id'], digest(self.m))
            if frozen and (frozen[0] != expected[0] or
                           (frozen[1] != expected[1] and not (historical and expected[1] < frozen[1]))):
                raise Rejected('authorization budget/prior usage cannot reset between batches')
            if not frozen and expected[1] != 1_000_000:
                raise Rejected('initialize original authorization before explicit upgrade')
            db.execute('INSERT OR IGNORE INTO authorization VALUES (1,?,?)', expected)
            row = db.execute('SELECT identity, manifest_hash FROM batch WHERE identity=?', (self.m['batch_id'],)).fetchone()
            if row and row != (self.m['batch_id'], digest(self.m)):
                raise Rejected('ledger belongs to another frozen batch/configuration')
            db.execute('INSERT OR IGNORE INTO batch VALUES (?,?,?)', (self.m['batch_id'], digest(self.m), time.time()))

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def event(self, kind, body):
        with self.connect() as db:
            db.execute('INSERT INTO events VALUES (?,?,?)', (time.time(), kind, json.dumps(body, sort_keys=True)))

    def halt(self, reason):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO halted VALUES (?,?,?)', (self.m['batch_id'], reason, time.time()))

    def halt_reason(self):
        with self.connect() as db:
            row = db.execute('SELECT reason FROM halted WHERE batch_id=?', (self.m['batch_id'],)).fetchone()
        return row[0] if row else None

    def reserve(self, identity, sample, bound, correlation):
        positive(bound, 'request allocation')
        l = self.m['limits']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT token_limit FROM authorization WHERE singleton=1').fetchone() != (l['batch_tokens'],):
                raise Rejected('historical batch is read-only after authorization upgrade')
            if db.execute('SELECT 1 FROM halted WHERE batch_id=?', (self.m['batch_id'],)).fetchone():
                raise Rejected('batch stopped; no additional provider sends')
            if db.execute('SELECT 1 FROM sends WHERE state="uncertain"').fetchone():
                raise Rejected('uncertain provider send; reconcile without automatic resend')
            if time.time() - db.execute('SELECT started FROM batch WHERE identity=?', (self.m['batch_id'],)).fetchone()[0] > l['batch_seconds']:
                raise Rejected('batch deadline reached')
            if db.execute('SELECT 1 FROM sends WHERE id=?', (identity,)).fetchone():
                raise Rejected('same provider operation cannot be resent')
            total, count = db.execute('SELECT COALESCE(SUM(COALESCE(actual,bound)),0),COUNT(*) FROM sends').fetchone()
            used, calls = db.execute('SELECT COALESCE(SUM(COALESCE(actual,bound)),0),COUNT(*) FROM sends WHERE sample=? AND batch_id=?', (sample, self.m['batch_id'])).fetchone()
            if (bound > l['request_tokens'] or total + bound + l['prior_tokens'] > l['batch_tokens']
                    or used + bound > l['scenario_tokens'] or count + l.get('prior_requests', 1) >= l['requests'] or calls >= l['scenario_requests']):
                raise Rejected('cumulative/request/scenario safety envelope exhausted')
            db.execute('INSERT INTO sends (id,sample,bound,actual,state,correlation,batch_id) VALUES (?,?,?,NULL,?,?,?)', (identity, sample, bound, 'sending', json.dumps(correlation), self.m['batch_id']))

    def settle(self, identity, usage):
        known = (usage.get('usage_source') == 'provider' and type(usage.get('total_tokens')) is int
                 and usage['total_tokens'] >= 0)
        with self.connect() as db:
            row = db.execute('SELECT bound,state FROM sends WHERE id=?', (identity,)).fetchone()
            if not row or row[1] != 'sending':
                raise Rejected('unmatched provider settlement')
            if known and usage['total_tokens'] <= row[0]:
                db.execute('UPDATE sends SET actual=?,state="settled" WHERE id=?', (usage['total_tokens'], identity))
            else:
                # Over-bound observations remain charged in full, even though the batch stops.
                db.execute('UPDATE sends SET actual=?,state="uncertain" WHERE id=?', (usage.get('total_tokens') if known else None, identity))
        if not known or usage['total_tokens'] > row[0]:
            raise Rejected('missing usage or breached conservative allocation; stop batch')

    def uncertain(self, identity):
        with self.connect() as db:
            db.execute('UPDATE sends SET state="uncertain" WHERE id=? AND state="sending"', (identity,))

    def summary(self):
        with self.connect() as db:
            used, held, calls = db.execute('SELECT COALESCE(SUM(actual),0),COALESCE(SUM(CASE WHEN actual IS NULL THEN bound ELSE 0 END),0),COUNT(*) FROM sends').fetchone()
            unknown = db.execute('SELECT COUNT(*) FROM sends WHERE state="uncertain"').fetchone()[0]
            abandoned = db.execute('SELECT COUNT(*) FROM sends WHERE state="abandoned_held"').fetchone()[0]
            inflight = db.execute('SELECT COUNT(*) FROM sends WHERE state="sending"').fetchone()[0]
        return dict(prior_tokens=self.m['limits']['prior_tokens'], measured_tokens=used,
                    held_tokens=held, cumulative_tokens=used + held + self.m['limits']['prior_tokens'],
                    requests=calls, prior_requests=self.m['limits'].get('prior_requests', 1), cumulative_requests=calls + self.m['limits'].get('prior_requests', 1), uncertain_requests=unknown, abandoned_held_requests=abandoned, inflight_requests=inflight)

    def batch_sends(self, sample=None):
        """Evidence is batch-local even though cost accounting is campaign-wide."""
        with self.connect() as db:
            rows = db.execute('SELECT id,sample,correlation FROM sends WHERE batch_id=?',
                              (self.m['batch_id'],)).fetchall()
        return [(identity, current, json.loads(correlation))
                for identity, current, correlation in rows
                if sample is None or current == sample]

    def consume(self, sample, target, kind):
        """Durable one-shot match, never consumes another invocation after restart."""
        identity = f'{self.m["batch_id"]}:{sample}:{kind}'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT target FROM faults WHERE id=?', (identity,)).fetchone()
            if row:
                return False
            db.execute('INSERT INTO faults VALUES (?,?,?,?)', (identity, sample, json.dumps(target, sort_keys=True), 'consumed'))
            return True
