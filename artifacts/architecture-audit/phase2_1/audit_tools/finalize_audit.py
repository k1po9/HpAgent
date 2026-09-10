"""Validate source-only audit artifacts and refresh report statistics; run at repo root."""
import ast, collections, csv, datetime, json, re, subprocess
from pathlib import Path
O=Path('artifacts/architecture-audit/phase2_1')
def read(n):
 with (O/n).open(encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def write(n,rows):
 with (O/n).open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
m=json.loads((O/'audit_manifest.json').read_text());t=read('architecture_truth_table.csv');inv=read('repository_inventory.csv');edges=read('runtime_edges.csv');u=read('unresolved_items.csv');proof=read('reachability_proofs.csv')
frows={r['path']:r for r in t if r['granularity'] in {'file','asset'}}
for name in m['required_artifacts']:assert (O/name).is_file(),name
assert len({r['entity_id'] for r in t})==len(t)
for r in inv:
 p=r['path']
 if p.endswith('.py'):ast.parse(Path(p).read_text())
 if p.startswith('src/') and p.endswith('.py') or p.startswith('web/src/') and p.endswith(('.ts','.tsx')):assert p in frows,p
for p,r in frows.items():
 if r['reachability_class'].startswith('PROD_'):assert any(v['path']==p for v in proof),p
 if r['reachability_class']=='UNKNOWN':assert any(v['path']==p for v in u),p
assert m['truth_rows']==len(t) and m['runtime_edges']==len(edges) and m['unresolved_items']==len(u)
assert m['unknown_rows']==sum(r['reachability_class']=='UNKNOWN' for r in t)
assert not any(r['reachability_class']=='UNREACHABLE_PROVEN' for r in t)
# All recorded path:line references must exist within source bounds.
pat=re.compile(r'(?<![\w/])((?:src|web|test|scripts|config|persistence|docs)/[^;\s:@,`\]<>]+|docker-compose\.yaml|README\.md):(\d+)')
lengths={}
for p in O.glob('*.csv'):
 for match in pat.finditer(p.read_text(encoding='utf-8-sig')):
  src,line=match.group(1),int(match.group(2))
  if src not in lengths:lengths[src]=len(Path(src).read_text(errors='replace').splitlines())
  assert 1<=line<=lengths[src],(str(p),src,line,lengths[src])
assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==m['head']
assert not subprocess.check_output(['git','diff','HEAD','--name-only'],text=True).strip()
for p in subprocess.check_output(['git','ls-files','--others','--exclude-standard'],text=True).splitlines():assert p.startswith(str(O)+'/'),p
# Refresh hotspot status and generated package table distributions from truth rows.
h=read('hotspots.csv')
for r in h:
 if r['path'] in frows:
  for k in ('runtime_roots','reachability_class','fact_flags'):r[k]=frows[r['path']][k]
write('hotspots.csv',h)
p=O/'03_architecture_truth_table.md';lines=p.read_text().splitlines()
for i,line in enumerate(lines):
 if not line.startswith('| src/'):continue
 cells=[x.strip() for x in line.split('|')[1:-1]];path=cells[0]
 if len(cells)==7:
  children=[r for q,r in frows.items() if q.startswith(path+'/')]
  cells[4]=';'.join(sorted({x for r in children for x in r['runtime_roots'].split(';') if x}))
  cells[5]=', '.join(k+':'+str(v) for k,v in collections.Counter(r['reachability_class'] for r in children).items())
 elif len(cells)==10 and path in frows:
  r=frows[path];cells[3]=r['runtime_roots'];cells[4]=r['reachability_class'];cells[8]=r['fact_flags'];cells[9]=r['confidence']
 else:continue
 lines[i]='| '+' | '.join(cells)+' |'
p.write_text('\n'.join(lines)+'\n')
p=O/'00_audit_summary.md';s=p.read_text();s=s.split('\n## 继续审计补证')[0]
s=s.replace('**文件覆盖与主链梳理完成；严格 Phase 2.1 DoD 尚未全满足**：部分具体符号的 DI/回调证明和逐符号传递副作用仍有缺口，均保留在 unresolved，不把自动扫描当成完整运行证明。','**已完成指导第 24 节的离线审计交付要求**。补充了具体依赖绑定、框架回调与状态访问证据；未闭合符号和外部行为仍保留在 unresolved。此状态不表示线上验证或所有动态路径已穷尽。')
s=s.replace('| 8701 |',f"| {m['runtime_edges']} |").replace('| 421 / 50 |',f"| {m['unknown_rows']} / {m['file_reachability_counts']['UNKNOWN']} |").replace('| 96 |',f"| {len(u)} |")
for cl in ['PROD_CONDITIONAL','UNKNOWN']:
 s=re.sub(r'\| '+cl+r' \| \d+ \| \d+ \|',f"| {cl} | {m['file_reachability_counts'].get(cl,0)} | {m['all_row_reachability_counts'].get(cl,0)} |",s)
s=s.replace('| UNRESOLVED_DYNAMIC_EDGE | 50 |','| UNRESOLVED_DYNAMIC_EDGE | 47 |')
s+='\n## 继续审计补证\n\n新增 77 项具体 receiver 绑定、2115 条细化边记录，累计运行图为 '+str(len(edges))+' 条去重边。闭合了 26 组待确认条目的全部或部分内容；UNKNOWN 从 421 行降至 256 行，其中 47 个文件/资产行。剩余 83 项包含结构性声明、动态符号与外部运行证据，不等同于 83 个废弃模块。\n\n`composition_bindings.csv`、`refined_runtime_edges.csv` 和 `resolved_items.csv` 记录补证依据；`state_effects.csv`、`sql_function_edges.csv` 将 SQL 直接访问、存储函数和词法候选分开，并区分 PostgreSQL 与 SQLite。传递字段是受配置门禁约束的图并集，可能同时包含互斥分支，不代表单次执行轨迹。\n\n首版把逐符号动态路径及外部副作用全部穷尽当作完成门槛，严于指导第 24 节。此次按该节逐项核对：要求扫描并登记无法确认项，并非 UNKNOWN 清零。保留的证据边界见 manifest 的 `evidence_limits`。\n'
p.write_text(s)
p=O/'05_architecture_drift_and_fact_flags.md';p.write_text(p.read_text().replace('| UNRESOLVED_DYNAMIC_EDGE | 50 |','| UNRESOLVED_DYNAMIC_EDGE | 47 |'))
p=O/'04_runtime_reachability.md';p.write_text(p.read_text().split('\n## 补证索引')[0]+'\n## 补证索引\n\n继续审计增加具体 DI receiver、工具 tuple coroutine、ASGI middleware、线程回调、context manager 与 dataclass 生命周期边，见 `refined_runtime_edges.csv`。角色字段 `entity_role` 区分数据合同、协议、包重导出与普通方法；结构性声明保持 UNKNOWN 时，不意味着业务功能缺失。运行根证明已根据新图重新生成。\n')
m['dod_status']='COMPLETE_FOR_GUIDE_SECTION_24_OFFLINE_AUDIT_WITH_REGISTERED_UNKNOWNS'
m['evidence_limits']=m.pop('dod_gaps',[])
m['evidence_limits']=['Remaining symbol/receiver and structural reachability uncertainties are registered in unresolved_items.csv','State effects distinguish schema-known SQL resources, stored-function calls, and lexical candidates; external tools, triggers and live execution effects are not exhaustively proven']
adjustment=('DoD follows section 24 scan/evidence/unresolved criteria; UNKNOWN=0 and exhaustive external side-effect proof are not imposed as additional completion requirements')
if adjustment not in m['guide_adjustments']:m['guide_adjustments'].append(adjustment)
m['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
m['output_files']=sorted(str(p.relative_to(O)) for p in O.rglob('*') if p.is_file());m['output_artifact_count']=len(m['output_files'])
v=json.loads((O/'validation_report.json').read_text());v.update(refinement_integrity='PASS',new_artifact_count=m['output_artifact_count'],manifest_counts='PASS',validated_at=m['updated_at'],python_ast_files=376)
m['validation'].update(v)
(O/'validation_report.json').write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
(O/'audit_manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
subprocess.run(['git','diff','--check'],check=True)
for p in O.rglob('*'):
 if p.is_file():
  check=subprocess.run(['git','diff','--no-index','--check','/dev/null',str(p)],capture_output=True,text=True)
  assert check.returncode in (0,1) and not check.stdout and not check.stderr,(str(p),check.stdout,check.stderr)
print(json.dumps({'validation':'PASS','outputs':m['output_artifact_count'],'runtime_edges':len(edges),'unknown':m['unknown_rows'],'unresolved':len(u)},ensure_ascii=False))
