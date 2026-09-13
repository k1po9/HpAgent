"""Validate Phase 2.2 audit artifacts without importing or starting production code."""
from pathlib import Path
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

def read(path):
    with path.open(encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))

def check(name, condition, detail=''):
    checks[name] = 'PASS' if condition else 'FAIL'
    if not condition:
        errors.append(f'{name}: {detail}')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def unique(rows, key):
    return len(rows) == len({r[key] for r in rows})

manifest = json.loads((OUT / 'audit_manifest.json').read_text())
decisions = read(OUT / 'decision_register.csv')
evidence = read(OUT / 'evidence_register.csv')
dids = {r['decision_id'] for r in decisions}
eids = {r['evidence_id'] for r in evidence}
gates = {f'G{i:02d}' for i in range(1, 12)}
check('unique_decisions', unique(decisions, 'decision_id') and len(decisions) == 16)
check('unique_evidence', unique(evidence, 'evidence_id') and len(evidence) == 25)
check('all_decisions_proposed', all(r['status'] == 'PROPOSED' for r in decisions))
for d in decisions:
    for key in ('recommendation','rationale','alternatives','impact','rollback','logical_owner','gate_ids'):
        check(f"{d['decision_id']}_{key}", bool(d[key].strip()))
    check(f"{d['decision_id']}_evidence_refs", set(d['evidence_ids'].split(';')) <= eids)
    check(f"{d['decision_id']}_gate_refs", set(d['gate_ids'].split(';')) <= gates)
    for path in d['validation_refs'].split(';'):
        check(f"validation_ref:{path}", (ROOT / path).is_file())
for e in evidence:
    path = ROOT / e['path']
    check(f"evidence_path:{e['evidence_id']}", path.is_file())
    if path.is_file():
        check(f"evidence_line:{e['evidence_id']}", 1 <= int(e['line']) <= len(path.read_text().splitlines()))
        check(f"evidence_hash:{e['evidence_id']}", sha(path) == e['sha256'])
for filename, source, sourcekey, key in (
    ('hypothesis_decisions.csv','hypothesis_results.csv','id','hypothesis_id'),
    ('drift_dispositions.csv','architecture_drift.csv','id','drift_id'),
    ('unresolved_dispositions.csv','unresolved_items.csv','item_id','item_id'),
):
    rows = read(OUT / filename)
    check(filename+'_coverage', unique(rows,key) and {r[key] for r in rows} == {r[sourcekey] for r in read(OLD/source)})
    check(filename+'_decision_refs', all(set(r['decision_ids'].split(';')) <= dids for r in rows))
    if filename == 'unresolved_dispositions.csv':
        check('unresolved_not_silently_closed', all(r['closed_in_phase2_2'] == 'false' for r in rows))
        check('unresolved_gate_refs', all(set(r['gate_ids'].split(';')) <= gates for r in rows))
truth = read(OLD / 'architecture_truth_table.csv')
expected_hotspots = {r['path'] for r in truth if r['granularity'] in ('file','asset') and 'LARGE_HOTSPOT' in r['fact_flags'].split(';')}
hotspots = read(OUT / 'hotspot_dispositions.csv')
check('all_26_large_file_asset_hotspots', unique(hotspots,'path') and {r['path'] for r in hotspots} == expected_hotspots and len(hotspots) == 26)
check('hotspot_decision_refs', all(set(r['decision_ids'].split(';')) <= dids for r in hotspots))
for group in ('phase2_1_input_sha256','protected_tracked_file_sha256'):
    changed = [path for path, digest in manifest[group].items() if not (ROOT/path).is_file() or sha(ROOT/path) != digest]
    check(group+'_unchanged', not changed, ', '.join(changed))
head = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
check('review_head_unchanged', head == manifest['review_head'])
current_diff = subprocess.check_output(['git','diff','--binary','HEAD'], cwd=ROOT, text=True).strip()
check('preexisting_user_diff_preserved', current_diff == manifest['initial_tracked_diff'])
source_delta = subprocess.check_output(['git','diff','--name-only',manifest['phase2_1_source_head'],manifest['review_head'],'--','src','web','config','persistence','test','scripts','tools','docker-compose.yaml','requirements.txt','pyproject.toml'], cwd=ROOT, text=True).strip()
check('business_code_matches_phase2_1_baseline', source_delta == '')
check('requirements_byte_equality', (ROOT/'requirements.txt').read_bytes() == (ROOT/'src/requirements.txt').read_bytes())
markdown = list(OUT.glob('*.md'))
all_text = '\n'.join(p.read_text() for p in markdown)
check('markdown_decision_refs', set(re.findall(r'ACD-\d{2}',all_text)) <= dids)
check('markdown_gate_refs', set(re.findall(r'G\d{2}',all_text)) <= gates)
for p in markdown:
    links = re.findall(r'\[[^\]]*\]\(([^)]+)\)',p.read_text())
    for link in links:
        if '://' not in link and not link.startswith('#'):
            check(f'local_link:{p.name}:{link}', (p.parent/link.split('#')[0]).exists())
package_paths = {r['path'].removeprefix('src/') for r in truth if r['granularity'] == 'package'}
boundary_text = (OUT/'02_target_boundaries.md').read_text()
check('all_30_backend_packages_mapped', len(package_paths) == 30 and all(f'| {p} |' in boundary_text for p in package_paths))
check('decision_markdown_matches_csv', all(f"## {d['decision_id']} · {d['title']}" in (OUT/'01_decision_register.md').read_text() and all(d[k] in (OUT/'01_decision_register.md').read_text() for k in ('recommendation','rationale','alternatives','impact','rollback')) for d in decisions))
for p in OUT.rglob('*'):
    if p.is_file() and p.suffix in ('.md','.py'):
        bad = [i for i,s in enumerate(p.read_text().splitlines(),1) if s.rstrip() != s]
        check('whitespace:'+str(p.relative_to(OUT)), not bad, str(bad))
check('git_diff_check', subprocess.run(['git','diff','--check'],cwd=ROOT,capture_output=True).returncode == 0)
report = dict(status='PASS' if not errors else 'FAIL',validated_at=datetime.now(timezone.utc).isoformat(),review_head=head,counts=dict(decisions=len(decisions),evidence=len(evidence),hypotheses=17,drifts=10,unresolved=83,large_file_asset_hotspots=len(hotspots),backend_packages=30),checks=checks,errors=errors,production_tests='NOT_RUN_DOCUMENTATION_ONLY_AUDIT',external_runtime='NOT_VERIFIED',semantic_review='Targeted source review by author; automated checks verify references/coverage/integrity, not architectural correctness or production readiness.')
(OUT/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('status','counts','errors')},ensure_ascii=False,indent=2))
raise SystemExit(bool(errors))
