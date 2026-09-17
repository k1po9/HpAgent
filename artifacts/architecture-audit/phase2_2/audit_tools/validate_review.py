"""Validate R2.1 provenance, target registers and edit scope; no product imports/services."""
from pathlib import Path
import ast
import csv
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'artifacts/architecture-audit/phase2_2'
OLD = OUT.parent / 'phase2_1'
errors = []
checks = {}


def read_csv(path):
    with path.open(encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def check(name, condition, detail=''):
    checks[name] = 'PASS' if condition else 'FAIL'
    if not condition:
        errors.append(f'{name}: {detail}')


def unique(rows, key):
    return len(rows) == len({r[key] for r in rows})


manifest = json.loads((OUT / 'audit_manifest.json').read_text())
register = json.loads((OUT / 'decision_register.json').read_text())
decisions = read_csv(OUT / 'decision_register.csv')
evidence = read_csv(OUT / 'evidence_register.csv')
retirements = read_csv(OUT / 'retirement_candidates.csv')
packages = read_csv(OUT / 'work_packages.csv')
dids = {f'ACD-{i:02d}' for i in range(1, 19)}
eids = {f'E{i:02d}' for i in range(1, 46)}
gates = {f'G{i:02d}' for i in range(1, 14)}
check('all_original_16_plus_two_new', unique(decisions, 'decision_id') and {r['decision_id'] for r in decisions} == dids)
check('45_current_fact_anchors', unique(evidence, 'evidence_id') and {r['evidence_id'] for r in evidence} == eids)
check('explicit_not_implemented', register['implementation_status'] == 'NOT_IMPLEMENTED' and all(r['implementation_status'] == 'NOT_IMPLEMENTED' and r['classification'] == 'TARGET_DECISION' for r in decisions))
projected = []
for d in register['decisions']:
    row = dict(revision='2.2-r2.1', classification='TARGET_DECISION', implementation_status='NOT_IMPLEMENTED')
    row.update({k: ';'.join(v) if isinstance(v, list) else v for k, v in d.items()})
    projected.append(row)
check('decision_json_csv_exact_projection', projected == decisions)
fields = ('current_fact', 'target_decision', 'required_refactor', 'future_option', 'rationale', 'tradeoff', 'rollback')
check('all_decision_fields_nonempty', all(all(r[k].strip() for k in fields) for r in decisions))
check('decision_evidence_and_gate_refs', all(set(r['evidence_ids'].split(';')) <= eids and set(r['gate_ids'].split(';')) <= gates for r in decisions))
bad_test_refs = sorted({p for r in decisions for p in r['validation_refs'].split(';') if not (ROOT / p).is_file()})
check('validation_refs_exist_not_test_pass_claim', not bad_test_refs, str(bad_test_refs))
bad_evidence = []
for e in evidence:
    p = ROOT / e['path']
    if not p.is_file() or sha(p) != e['sha256'] or not 1 <= int(e['line']) <= len(p.read_text().splitlines()):
        bad_evidence.append(e['evidence_id'])
check('evidence_paths_lines_hashes', not bad_evidence, str(bad_evidence))
check('all_evidence_is_current_fact', all(e['classification'] == 'CURRENT_FACT' for e in evidence))

tables = {}
for filename, source, sourcekey, key in (
    ('hypothesis_decisions.csv', 'hypothesis_results.csv', 'id', 'hypothesis_id'),
    ('drift_dispositions.csv', 'architecture_drift.csv', 'id', 'drift_id'),
    ('unresolved_dispositions.csv', 'unresolved_items.csv', 'item_id', 'item_id'),
):
    rows = read_csv(OUT / filename)
    tables[filename] = rows
    source_rows = read_csv(OLD / source)
    check(filename + '_complete_unique', unique(rows, key) and {r[key] for r in rows} == {r[sourcekey] for r in source_rows})
    check(filename + '_decision_refs', all(set(r['decision_ids'].split(';')) <= dids for r in rows))
urows = tables['unresolved_dispositions.csv']
source_u = {r['item_id']: r for r in read_csv(OLD / 'unresolved_items.csv')}
check('original_unresolved_facts_preserved', all(r['source_fact_closed'] == 'false' and r['phase2_1_status'] == source_u[r['item_id']]['evidence_status'] and r['phase2_1_kind'] == source_u[r['item_id']]['kind'] for r in urows))
check('old_production_gates_removed', all(r['old_production_evidence_required'] == 'false' for r in urows + retirements) and {r['item_id'] for r in urows if r['decision_gate_removed'] == 'true'} == {'U-DEPLOY', 'U-HISTORY', 'U-ACCOUNTS'})
check('unresolved_target_gate_refs', all(set(r['gate_ids'].split(';')) <= gates for r in urows))
truth = read_csv(OLD / 'architecture_truth_table.csv')
expected_hotspots = {r['path'] for r in truth if r['granularity'] in ('file', 'asset') and 'LARGE_HOTSPOT' in r['fact_flags'].split(';')}
hotspots = read_csv(OUT / 'hotspot_dispositions.csv')
check('26_file_asset_hotspots_covered', unique(hotspots, 'path') and {r['path'] for r in hotspots} == expected_hotspots and len(hotspots) == 26)
check('hotspot_decision_refs', all(set(r['decision_ids'].split(';')) <= dids for r in hotspots))

for group in ('phase2_1_input_sha256', 'protected_tracked_file_sha256'):
    changed = [p for p, digest in manifest[group].items() if not (ROOT / p).is_file() or sha(ROOT / p) != digest]
    check(group + '_unchanged', not changed, str(changed))
outside = ('.', ':(exclude)artifacts/architecture-audit/phase2_2')
outside_diff = git('diff', '--binary', 'HEAD', '--', *outside)
check('outside_scope_existing_user_diff_preserved', outside_diff == manifest['outside_scope_diff_at_revision_start'])
check('outside_scope_no_new_untracked_files', git('ls-files', '--others', '--exclude-standard', '--', *outside).splitlines() == manifest['outside_scope_untracked_at_revision_start'])
# HEAD is provenance, not an equality gate: committing an audit must not invalidate it.
check('no_committed_source_drift_since_review', git('diff', '--name-only', manifest['review_head'], 'HEAD', '--', *outside) == '')
check('business_code_matches_phase2_1_baseline', git('diff', '--name-only', manifest['phase2_1_source_head'], manifest['review_head'], '--', 'src', 'web', 'config', 'persistence', 'test', 'scripts', 'tools', 'docker-compose.yaml', 'requirements.txt', 'pyproject.toml') == '')
check('requirements_current_byte_equality', (ROOT / 'requirements.txt').read_bytes() == (ROOT / 'src/requirements.txt').read_bytes())

md_paths = sorted(OUT.glob('*.md'))
texts = {p.name: p.read_text() for p in md_paths}
all_text = '\n'.join(texts.values())
check('all_nine_narrative_artifacts', {p.name[:2] for p in md_paths} == {f'{i:02d}' for i in range(9)})
check('historical_labels_in_all_narratives', all('Historical architecture evidence. Not current architecture documentation.' in t for t in texts.values()))
check('markdown_decision_gate_refs', set(re.findall(r'ACD-\d{2}', all_text)) <= dids and set(re.findall(r'G\d{2}', all_text)) <= gates)
check('all_13_target_gate_definitions', set(re.findall(r'^\| (G\d{2}) ', texts['03_delivery_sequence_and_gates.md'], re.M)) == gates)
check('decision_narrative_matches_register', all(f"## {r['decision_id']} · {r['title']}" in texts['01_decision_register.md'] and all(r[k] in texts['01_decision_register.md'] for k in fields) for r in decisions))
check('decision_distinctions_in_each_entry', all(texts['01_decision_register.md'].count(label) == 18 for label in ('**CURRENT FACT / Current：**', '**TARGET DECISION / Target：**', '**Required migration/refactor：**', '**FUTURE OPTION：**')))
bad_links = []
for p in md_paths:
    for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', p.read_text()):
        if '://' not in link and not link.startswith('#') and not (p.parent / link.split('#')[0]).exists():
            bad_links.append(f'{p.name}: {link}')
check('local_markdown_links', not bad_links, str(bad_links))
check('balanced_fenced_blocks', all(t.count('```') % 2 == 0 for t in texts.values()))
backend = {r['path'].removeprefix('src/') for r in truth if r['granularity'] == 'package'}
check('all_30_backend_packages_mapped', len(backend) == 30 and all(f'| {p} |' in texts['02_target_boundaries.md'] for p in backend))
check('work_package_ids', unique(packages, 'work_package_id') and {r['work_package_id'] for r in packages} == {f'W{i}' for i in range(8)})
seen = set()
dag_valid = True
for p in packages:
    deps = set(filter(None, p['depends_on'].split(';')))
    dag_valid = dag_valid and deps <= seen
    seen.add(p['work_package_id'])
check('work_packages_acyclic_in_delivery_order', dag_valid)
check('package_decision_gate_refs', all(set(r['decision_ids'].split(';')) <= dids and set(r['gate_ids'].split(';')) <= gates for r in packages))
check('retirement_refs', unique(retirements, 'candidate_id') and all(set(r['decision_ids'].split(';')) <= dids and set(r['gate_ids'].split(';')) <= gates and (ROOT / r['path']).exists() and r['implementation_status'] == 'NOT_IMPLEMENTED' for r in retirements))
bad_symbols = []
for r in retirements:
    if not r['symbol'] or not r['path'].endswith('.py'):
        continue
    tree = ast.parse((ROOT / r['path']).read_text())
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    if r['symbol'] not in names:
        bad_symbols.append(r['candidate_id'])
check('retirement_symbols_real_not_invented', not bad_symbols, str(bad_symbols))
counts = dict(decisions=len(decisions), evidence=len(evidence), hypotheses=len(tables['hypothesis_decisions.csv']),
              drifts=len(tables['drift_dispositions.csv']), unresolved=len(urows), large_file_asset_hotspots=len(hotspots),
              backend_packages=len(backend), work_packages=len(packages), retirement_candidates=len(retirements))
check('manifest_counts', counts == manifest['counts'])
bad_whitespace = []
for p in OUT.rglob('*'):
    if p.is_file() and p.suffix in ('.md', '.py', '.json'):
        if any(s.rstrip() != s for s in p.read_text().splitlines()):
            bad_whitespace.append(str(p.relative_to(OUT)))
check('artifact_whitespace', not bad_whitespace, str(bad_whitespace))
check('git_diff_check', subprocess.run(['git', 'diff', '--check'], cwd=ROOT, capture_output=True).returncode == 0)
report = dict(revision='2.2-r2.1', classification='HISTORICAL_AUDIT_VALIDATION',
              status='PASS' if not errors else 'FAIL', validated_at=datetime.now(timezone.utc).isoformat(),
              review_head=manifest['review_head'], checked_head=git('rev-parse', 'HEAD'),
              counts=counts, checks=checks, errors=errors,
              production_tests='NOT_RUN_ARCHITECTURE_DECISION_REVISION_ONLY',
              implementation_status='NOT_IMPLEMENTED',
              old_production_evidence='NOT_REQUIRED_BY_USER_PREMISE_NOT_MEASURED_AS_ZERO',
              semantic_review='Targeted author review. Automated validation proves register/link/coverage/scope consistency, not implementation, complete runtime reachability, or production readiness.')
(OUT / 'validation_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: report[k] for k in ('status', 'counts', 'errors')}, ensure_ascii=False, indent=2))
raise SystemExit(bool(errors))
