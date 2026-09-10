"""One-shot, source-read-only audit refinement. Run from repository root.

Reads first-pass CSVs, never imports application modules or calls business systems.
Writes only artifacts/architecture-audit/phase2_1. Exact bindings below were reviewed
against this audit HEAD; this is not a general-purpose sound Python call graph.
"""
import ast,collections,csv,json,re,subprocess
from pathlib import Path
O=Path('artifacts/architecture-audit/phase2_1')
def read(n):return list(csv.DictReader((O/n).open(encoding='utf-8-sig')))
def emit(n,rows,fields=None):
 rows=list(rows)
 with (O/n).open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=fields or list(rows[0]),lineterminator='\n',extrasaction='ignore');w.writeheader();w.writerows(rows)
inv=read('repository_inventory.csv');truth=read('architecture_truth_table.csv');E=read('runtime_edges.csv');roots=read('runtime_roots.csv');defs={};scopes={};parent={};trees={};texts={};aliases={};bymod={};mod={}
for v in inv:
 p=v['path']
 if p.endswith(('.py','.sql','.md')):texts[p]=Path(p).read_text(errors='replace')
 if not p.endswith('.py'):continue
 trees[p]=ast.parse(texts[p]);m=(p[4:] if p.startswith('src/') else p).removesuffix('.py').replace('/','.').removesuffix('.__init__');mod[p]=m;bymod[m]=p
for p,t in trees.items():
 aliases[p]={}
 def index(n,scope=''):
  if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):scope=(scope+'.' if scope else '')+n.name;defs[p+'::'+scope]=(p,n)
  scopes[id(n)]=scope
  for ch in ast.iter_child_nodes(n):parent[id(ch)]=n;index(ch,scope)
 index(t)
 for n in ast.walk(t):
  if isinstance(n,ast.Import):
   for a in n.names:aliases[p][a.asname or a.name.split('.')[0]]=a.name if a.asname else a.name.split('.')[0]
  elif isinstance(n,ast.ImportFrom):
   pkg=mod[p] if p.endswith('/__init__.py') else mod[p].rpartition('.')[0]
   prefix='.'.join(pkg.split('.')[:len(pkg.split('.'))-n.level+1]) if n.level else ''
   base='.'.join(x for x in [prefix,n.module or ''] if x)
   for a in n.names:aliases[p][a.asname or a.name]=base+'.'+a.name

def full(s,seen=None):
 seen=set() if seen is None else seen
 if s in seen:return None
 seen.add(s);parts=s.split('.')
 for i in range(len(parts),0,-1):
  p=bymod.get('.'.join(parts[:i]));tail='.'.join(parts[i:])
  if not p:continue
  if not tail:return p
  k=p+'::'+tail
  if k in defs:return k
  first,*more=tail.split('.')
  if first in aliases[p]:return full(aliases[p][first]+('.'+'.'.join(more) if more else ''),seen)
 return None

def resolve(p,n,scope):
 if n is None:return None
 if isinstance(n,ast.Constant) and isinstance(n.value,str):
  try:return resolve(p,ast.parse(n.value,mode='eval').body,scope)
  except SyntaxError:return None
 if isinstance(n,ast.Name):
  s=scope
  while True:
   k=p+'::'+(s+'.' if s else '')+n.id
   if k in defs:return k
   if not s:break
   s=s.rpartition('.')[0]
  return full(aliases[p][n.id]) if n.id in aliases[p] else None
 if isinstance(n,ast.Attribute):
  b=resolve(p,n.value,scope)
  if b and b+('.' if '::' in b else '::')+n.attr in defs:return b+('.' if '::' in b else '::')+n.attr
  raw=ast.unparse(n).split('.')
  if raw[0] in aliases[p]:return full(aliases[p][raw[0]]+'.'+'.'.join(raw[1:]))
 if isinstance(n,ast.Call):
  k=resolve(p,n.func,scope)
  if k in defs:
   pp,nn=defs[k]
   if isinstance(nn,ast.ClassDef):return k
   if getattr(nn,'returns',None):return resolve(pp,nn.returns,scopes[id(nn)])
 if isinstance(n,ast.Await):return resolve(p,n.value,scope)
 return None

def ref(p,term):
 for i,line in enumerate(Path(p).read_text().splitlines(),1):
  if term in line:return f'{p}:{i}'
 raise ValueError((p,term))
new=[];bindings=[]
def add(source,target,kind,evidence,gate='',detail=''):
 if not target:return
 new.append(dict(source=source,target=target,edge_type=kind,evidence=evidence,config_gates=gate,confidence='HIGH',detail=detail))
def key(p,s):
 k=p+'::'+s
 assert k in defs,k
 return k
# Exact receiver bindings from concrete composition, not a global guess by method name.
def bind(p,owner,receiver,q,cls,ep,term,gate='injected concrete service; request/config branch'):
 key(q,cls)
 if owner:key(p,owner)
 bindings.append(dict(caller_owner=p+('::'+owner if owner else ''),receiver=receiver,concrete_type=q+'::'+cls,evidence=ref(ep,term),config_gates=gate))
qq='src/agent_execution/qq_host.py';boot='src/bootstrap/qq.py';worker='src/orchestration/worker.py'
for cls in ['QQLegacyContextProvider','QQLegacyRequestLoader','TurnMemoryQQRetentionSink','TurnMemoryQQAuditSink','TurnMemoryQQAuditSinkFactory']:
 bind(qq,cls,'self._memory','src/application/memory.py','TurnMemoryService',boot,'memory = TurnMemoryService')
for cls in ['ReplyServiceQQSink','ReplyServiceQQEventSink','ReplyServiceQQEventSinkFactory']:
 bind(qq,cls,'self._reply','src/application/reply.py','ReplyService',boot,'reply = ReplyService')
for cls in ['QQLegacyContextProvider','QQLegacyRequestLoader']:
 bind(qq,cls,'self._context','src/harness/context_builder.py','HarnessContextBuilder',worker,'context_builder = HarnessContextBuilder')
for field,q,c in [('self._events',qq,'ReplyServiceQQEventSinkFactory'),('self._audit',qq,'TurnMemoryQQAuditSinkFactory'),('self._retention',qq,'TurnMemoryQQRetentionSink'),('self._loader',qq,'QQLegacyRequestLoader'),('self._replies',qq,'ReplyServiceQQSink')]:bind(qq,'QQExecutionHost',field,q,c,boot,c+'(')
for field,q,c in [('self._sandbox','src/sandbox/sandbox_manager.py','SandboxManager'),('self._session','src/session/store.py','SessionStore'),('self._model','src/resources/resource_pool.py','ResourcePool'),('self._prompts','src/harness/prompts.py','PromptLoader')]:bind('src/actions/runtime.py','ActionRuntime',field,q,c,boot,'actions = ActionRuntime')
for p,cl in [('src/application/session_archive.py','SessionArchiveService'),('src/application/memory_reflection.py','MemoryReflectionService'),('src/application/metrics.py','MetricsSnapshotService')]:bind(p,cl,'self._memory','src/application/memory.py','TurnMemoryService',boot,'memory = TurnMemoryService')
bind('src/session/store.py','SessionStore','self._hindsight','src/memory/hindsight_client.py','HindsightClient',worker,'hindsight_client = HindsightClient','hindsight.enabled and constructed client')
bind('src/session/store.py','SessionStore','self._cache','src/storage/redis.py','RedisCache',worker,'redis_cache = RedisCache','Redis configured and cache constructed')
bind('src/brain/engine.py','BrainEngine','self._model','src/resources/resource_pool.py','ResourcePool',worker,'resource_pool = ResourcePool')
bind('src/resources/resource_pool.py','ResourcePool','client','src/resources/model_client.py','ModelClient','src/resources/resource_pool.py','ModelClient(','selected initialized model endpoint')
for field,q,c in [('self.loader','src/agent_execution/web_adapters.py','PostgresWebRequestLoader'),('self.brain','src/brain/engine.py','BrainEngine'),('self.actions','src/actions/runtime.py','ActionRuntime'),('self.resource_prep','src/workspace/isolation.py','SessionResourceRecoveryService'),('self.lifecycle','src/web_domain/lifecycle.py','WebRunLifecycleService'),('self.approval_service','src/file_domain/approvals.py','FileActionApprovalService'),('self.persistent_file_service','src/file_domain/persistent.py','PersistentWebFileService')]:bind('src/agent_activities/runtime.py','DurableAgentActivities',field,q,c,worker,'durable_activities = DurableAgentActivities')
for receiver,q,c in [('self._loader','src/agent_execution/web_adapters.py','PostgresWebRequestLoader'),('self._replies','src/agent_execution/web_adapters.py','LifecycleWebReplySink'),('self._events','src/agent_execution/tracing/sink.py','TracingWebEventSinkFactory'),('self._resource_prep','src/workspace/isolation.py','SessionResourceRecoveryService')]:bind('src/agent_execution/web_host.py','WebExecutionHost',receiver,q,c,worker,'web_host = WebExecutionHost')
for field,q,c in [('self.store','src/web_domain/workflow_execution.py','PostgresWorkflowExecutionStore'),('self.temporal','src/orchestration/web_dispatcher.py','TemporalClientAdapter'),('self.cancellation_finalizer','src/web_domain/lifecycle.py','WebRunLifecycleService')]:bind('src/orchestration/web_dispatcher.py','TemporalOutboxDispatcher',field,q,c,worker,'TemporalOutboxDispatcher(')
for field,q,c in [('self._store','src/orchestration/web_reconcile_adapters.py','LifecycleReconcileStore'),('self._temporal','src/orchestration/web_reconciler.py','TemporalInspectorAdapter')]:bind('src/orchestration/web_reconciler.py','WebRunReconciler',field,q,c,worker,'reconciler = WebRunReconciler')
for field,q,c in [('self.temporal','src/orchestration/artifact_dispatcher.py','TemporalArtifactClient'),('self.outbox','src/web_artifacts/outbox.py','ArtifactOutboxService')]:bind('src/orchestration/artifact_dispatcher.py','ArtifactOutboxDispatcher',field,q,c,worker,'artifact_dispatcher = ArtifactOutboxDispatcher')
for field,q,c in [('self.discovery','src/research_adapters/discovery.py','CompositeSourceDiscoveryProvider'),('self.content','src/research_adapters/web.py','StaticWebContentProvider'),('self.canonicalizer','src/research_adapters/web.py','W3libSourceCanonicalizer'),('self.synthesis','src/research_adapters/synthesis.py','ResourcePoolResearchSynthesizer'),('self.markdown_publisher','src/file_runtime/research_output.py','ResearchMarkdownPublisher')]:bind('src/research_activities/runtime.py','ResearchActivities',field,q,c,worker,'research_activities = ResearchActivities')
for owner,receiver,q,c,term in [('_setup_reflect_schedule','account_service','src/account/postgres_account_service.py','PostgresAccountService','_setup_reflect_schedule(client, deps.account_service'),('_run_web_dispatcher_loop','dispatcher','src/orchestration/web_dispatcher.py','WebOutboxDispatcher','_run_web_dispatcher_loop(web_dispatcher)'),('_run_web_reconciler_loop','reconciler','src/orchestration/web_reconciler.py','WebRunReconciler','_run_web_reconciler_loop(web_reconciler)')]:bind(worker,owner,receiver,q,c,worker,term)
# Main container fields typed as object originate from these explicit constructions.
for receiver,q,c,term in [('deps.sandbox_manager','src/sandbox/sandbox_manager.py','SandboxManager','sandbox_manager = SandboxManager'),('deps.account_service','src/account/postgres_account_service.py','PostgresAccountService','account_service=qq_runtime.account_service'),('deps.hindsight_client','src/memory/hindsight_client.py','HindsightClient','hindsight_client = HindsightClient'),('deps.run_file_workspace','src/workspace/file_scope.py','RunFileWorkspace','run_file_workspace = RunFileWorkspace')]:bind(worker,'',receiver,q,c,worker,term)
for method in ['start_monitor','send']:
 for p,c,gate in [('src/channels/napcat.py','NapCatChannel','channels.enabled=napcat'),('src/channels/official_qq.py','OfficialQQChannel','channels.enabled=official_qq')]:
  if p+'::'+c+'.'+method in defs:add(key(worker,'start_worker'),key(p,c+'.'+method),'CHANNEL_FACTORY_LIFECYCLE',ref(worker,'await ch.start_monitor'),gate,'actual selected factory class')
# Enumerate each native file tool tuple: declaration -> selected coroutine and input schema.
for p in ['src/sandbox/tools/local/file_read.py','src/sandbox/tools/local/file_write.py','src/sandbox/tools/local/file_analysis.py']:
 for n in ast.walk(trees[p]):
  if isinstance(n,ast.Assign) and isinstance(n.value,(ast.List,ast.Tuple)) and any(isinstance(x,ast.Name) and x.id=='definitions' for x in n.targets):
   scope=scopes[id(n)]
   for item in n.value.elts:
    if isinstance(item,ast.Tuple) and len(item.elts)>=4:
     name=ast.literal_eval(item.elts[0]);schema=resolve(p,item.elts[2],scope);callback=resolve(p,item.elts[3],scope)
     add(p+'::'+scope,callback,'LITERAL_TOOL_CALLBACK',f'{p}:{item.lineno}','Web file tools enabled; selected tool='+str(name),'tuple definitions consumed by StructuredTool.from_function')
     add(p+'::'+scope,schema,'TOOL_ARGUMENT_SCHEMA',f'{p}:{item.lineno}','Web file tools enabled; selected tool='+str(name))
# File format selection is a closed registry, not an unrestricted duck-type guess.
for owner,cap,c,q in [('inspect_pdf','pdf','PdfAdapter','pdf.py'),('read_pdf_pages','pdf','PdfAdapter','pdf.py'),('extract_pdf_tables','pdf','PdfAdapter','pdf.py'),('inspect_docx','docx','DocxAdapter','docx.py'),('read_docx_paragraphs','docx','DocxAdapter','docx.py'),('extract_docx_tables','docx','DocxAdapter','docx.py'),('inspect_workbook','xlsx','XlsxAdapter','xlsx.py'),('read_sheet_range','xlsx','XlsxAdapter','xlsx.py'),('inspect_presentation','pptx','PptxAdapter','pptx.py'),('read_slide','pptx','PptxAdapter','pptx.py'),('_read_default_view','fast_text','MarkItDownFastTextViewProvider','markitdown.py')]:bind('src/sandbox/tools/local/file_read.py','create_file_read_tools.'+owner,'adapter','src/file_adapters/'+q,c,'src/sandbox/tools/local/file_read.py','"'+cap+'"','FileAdapterRegistry capability='+cap)
bind('src/sandbox/tools/local/file_read.py','create_file_read_tools','adapters','src/file_runtime/registry.py','FileAdapterRegistry','src/sandbox/tools/local/file_read.py','adapters = registry or default_file_adapter_registry()')
# MCP transport factory has three explicit alternatives, all conditional.
mcp='src/sandbox/tools/adapters/mcp.py'
for c,gate in [('MCPSession','streamable HTTP'),('MCPSessionSSE','transport=sse or /sse URL'),('MCPSessionStdio','transport=stdio')]:
 for owner in ['MCPToolManager._connect_one','MCPToolManager.connect._connect_and_cache','_build_langchain_tool']:
  bind(mcp,owner,'session',mcp,c,mcp,c+'(',gate)
# Fresh AST reference scan for explicit calls, executor callbacks, exact constructor-return members, and bindings.
for p,t in trees.items():
 if not p.startswith('src/'):continue
 for n in ast.walk(t):
  scope=scopes[id(n)];source=p+('::'+scope if scope else '')
  expressions=[]
  if isinstance(n,ast.Call):
   expressions.append((n.func,'REFINED_CALL'))
   fun=ast.unparse(n.func)
   if fun.endswith(('to_thread','add_done_callback')) and n.args:expressions.append((n.args[0],'EXECUTOR_OR_DONE_CALLBACK'))
   if fun.endswith('register_handler') and len(n.args)>1:expressions.append((n.args[1],'HANDLER_CALLBACK'))
   if fun.endswith('from_function'):
    for kw in n.keywords:
     if kw.arg in ['func','coroutine','args_schema']:expressions.append((kw.value,'TOOL_CALLBACK_OR_SCHEMA'))
   if fun=='app.add_middleware' and n.args:
    k=resolve(p,n.args[0],scope)
    if k:
     add(source,k,'ASGI_MIDDLEWARE_CONSTRUCT',f'{p}:{n.lineno}','ASGI HTTP request')
     for method in ['__init__','__call__','dispatch']:
      if k+'.'+method in defs:add(source,k+'.'+method,'ASGI_MIDDLEWARE_HOOK',f'{p}:{n.lineno}','ASGI HTTP request')
  for ex,kind in expressions:
   raw=ast.unparse(ex);target=resolve(p,ex,scope)
   if target and target in defs:add(source,target,kind,f'{p}:{n.lineno}','caller branch holds','exact lexical reference '+raw)
   for b in bindings:
    owner=b['caller_owner']
    if not (source==owner or source.startswith(owner+'.') or ('::' not in owner and p==owner)):continue
    pref=b['receiver']+'.'
    if raw.startswith(pref) and re.fullmatch(r'\w+',raw[len(pref):]):
     k=b['concrete_type']+'.'+raw[len(pref):]
     if k in defs:add(source,k,'COMPOSITION_BOUND_CALL',f'{p}:{n.lineno};'+b['evidence'],b['config_gates'],raw+' -> '+b['concrete_type'])
  if isinstance(n,ast.ClassDef):
   k=p+'::'+scope
   if any('dataclass' in ast.unparse(d) for d in n.decorator_list) and k+'.__post_init__' in defs:add(k,k+'.__post_init__','DATACLASS_POST_INIT',f'{p}:{n.lineno}')
  if isinstance(n,(ast.With,ast.AsyncWith)):
   for item in n.items:
    k=resolve(p,item.context_expr,scope)
    if k:
     for method in (['__aenter__','__aexit__'] if isinstance(n,ast.AsyncWith) else ['__enter__','__exit__']):
      if k+'.'+method in defs:add(source,k+'.'+method,'CONTEXT_MANAGER_HOOK',f'{p}:{n.lineno}')
# One explicitly configured logging formatter callback.
add('src/common/logging.py::setup_logging','src/common/logging.py::_JsonFormatter.format','LOGGING_FORMATTER_HOOK',ref('src/common/logging.py','_JsonFormatter('),'configured JSON handler receives log record')
# Preserve guards and separate refined exact edges; no import-only edge added to business graph.
E=list({tuple(x.values()):x for x in E+new}.values());emit('runtime_edges.csv',E);emit('refined_runtime_edges.csv',new);emit('composition_bindings.csv',bindings)
# Recompute root proofs from explicit executable / framework / contract edges.
adj=collections.defaultdict(list)
for e in E:adj[e['source']].append(e)
reached=collections.defaultdict(set);paths={}
for root in roots:
 rid=root['id'];start=root['node'];q=collections.deque([start]);seen={start};paths[(rid,start)]=[]
 while q:
  n=q.popleft();reached[n].add(rid)
  for e in adj[n]:
   if e['target'] not in seen:seen.add(e['target']);paths[(rid,e['target'])]=paths[(rid,n)]+[e];q.append(e['target'])
file_hits=collections.defaultdict(list)
for n,rr in reached.items():
 p=n.split('::')[0]
 if Path(p).is_file():
  for rid in rr:file_hits[p].append((rid,n,paths[(rid,n)]))
old_rc={x['entity_id']:x['reachability_class'] for x in truth};proofs=[]
def join(v):return ';'.join(sorted(set(str(x) for x in v if x)))
for r in truth:
 p=r['path'];k=p+('::'+r['symbol'] if r['granularity']=='symbol' else '')
 hits=file_hits[p] if r['granularity'] in ['file','asset'] else [(rid,k,paths[(rid,k)]) for rid in reached[k]] if r['granularity']=='symbol' else []
 if hits and r['granularity']!='package':
  r['runtime_roots']=join(h[0] for h in hits)
  if r['reachability_class']=='UNKNOWN':r['reachability_class']='PROD_CONDITIONAL' if any(h[0]!='R8' for h in hits) else 'OPS_ADMIN';r['unresolved_reason']='';r['confidence']='MEDIUM';r['fact_flags']=join(x for x in r['fact_flags'].split(';') if x!='UNRESOLVED_DYNAMIC_EDGE')
  if r['granularity'] in ['file','asset']:
   rid,n,chain=min(hits,key=lambda h:(h[0] not in {'R1','R2','R6','R7','R8'},len(h[2])))
   r['proof_id']='PROOF:'+p
   proofs.append(dict(proof_id=r['proof_id'],path=p,root=rid,target=n,chain=' -> '.join([next(v['node'] for v in roots if v['id']==rid)]+[e['target'] for e in chain]),evidence=join(e['evidence'] for e in chain),gates=join(e['config_gates'] for e in chain),confidence='MEDIUM' if any(e['confidence']=='MEDIUM' for e in chain) else 'HIGH'))
# Symbol roles make structural unknowns legible without inventing a new reachability enum.
for r in truth:
 p=r['path'];r['entity_role']='';r['reachability_basis']=''
 if r['granularity']=='symbol':
  k=p+'::'+r['symbol'];n=defs.get(k,(None,None))[1]
  if isinstance(n,ast.ClassDef):
   bases=[ast.unparse(x) for x in n.bases]
   r['entity_role']='protocol_definition' if any(x.endswith('Protocol') for x in bases) else 'data_contract' if any(x.endswith(('TypedDict','Enum','StrEnum','BaseModel')) for x in bases) or any('dataclass' in ast.unparse(x) for x in n.decorator_list) else 'class_definition'
  else:r['entity_role']='callable_or_property'
 elif p.endswith('/__init__.py'):r['entity_role']='package_initializer_or_reexport'
 elif r['granularity'] in ['file','asset']:r['entity_role']='source_module' if p.endswith(('.py','.ts','.tsx')) else 'repository_asset'
 if r['reachability_class']=='UNKNOWN' and r['entity_role'] in ['package_initializer_or_reexport','protocol_definition','data_contract']:
  r['reachability_basis']='静态角色已确认；没有独立业务执行入口不等于无使用。保持 UNKNOWN 而不把 import 或类型声明升级为生产执行。'
 elif r['runtime_roots']:r['reachability_basis']='explicit call/constructor/framework/registry graph; see proof_id or runtime_edges'
# Exact direct state effects and bounded transitive projection along call edges.
access=read('data_accesses.csv'); effects=[]
known_tables={r['object_name'].split('.')[-1].lower() for r in read('schema_objects.csv') if r['object_kind']=='TABLE'}
for a in access:
 effects.append(dict(entity=a['path']+('::'+a['symbol'] if a['symbol'] else ''),kind='SQL_READ' if a['operation'] in ['FROM','JOIN'] else 'SQL_WRITE',resource=a['resource'],evidence=a['evidence'],scope='DIRECT_SQL_LITERAL' if a['resource'].split('.')[-1].lower() in known_tables or a['path']=='src/session/db.py' and a['resource'] in {'users','sessions'} else 'LEXICAL_EFFECT_CANDIDATE'))
# Schema functions: latest definition of same named signature in ordered migration assets.
funcs={}
for p in sorted(x['path'] for x in inv if x['path'].startswith('persistence/migrations/') and x['path'].endswith('.sql')):
 txt=texts[p]
 for m in re.finditer(r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([\w.]+)\s*\(',txt,re.I):
  tail=txt[m.start():];tag=re.search(r'\$(?:[\w]*)\$',tail)
  if not tag:continue
  end=tail.find(tag.group(),tag.end())
  if end<0:continue
  body=tail[tag.end():end];funcs[m.group(1)]=(p,txt[:m.start()].count('\n')+1,body)
sql_edges=[]
for name,(p,line,body) in funcs.items():
 body=re.sub(r'/\*.*?\*/|--[^\n]*',' ',body,flags=re.S)
 for m in re.finditer(r'\b(FROM|JOIN|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w.]+)',body,re.I):
  op=m.group(1).upper();resource=m.group(2)
  if resource.split('.')[-1].lower() not in known_tables:continue
  effects.append(dict(entity='SQL_FUNCTION:'+name,kind='SQL_READ' if op in ['FROM','JOIN'] else 'SQL_WRITE',resource=resource,evidence=f'{p}:{line}',scope='MIGRATION_FUNCTION_BODY'))
for p,t in trees.items():
 if not p.startswith('src/'):continue
 for n in ast.walk(t):
  if isinstance(n,ast.Constant) and isinstance(n.value,str):
   for m in re.finditer(r'\bSELECT\s+([\w.]+)\s*\(',n.value,re.I):
    if m.group(1) in funcs:sql_edges.append(dict(source=p+('::'+scopes[id(n)] if scopes[id(n)] else ''),target='SQL_FUNCTION:'+m.group(1),edge_type='SQL_FUNCTION_CALL',evidence=f'{p}:{n.lineno}',config_gates='SQL transaction reaches call',confidence='HIGH',detail='schema definition inspected'))
# Annotated filesystem/network resources are concrete direct calls, not inferred from module names.
fs_ops={'read_text':'FILESYSTEM_READ','read_bytes':'FILESYSTEM_READ','write_text':'FILESYSTEM_WRITE','write_bytes':'FILESYSTEM_WRITE','unlink':'FILESYSTEM_DELETE','mkdir':'FILESYSTEM_WRITE','replace':'FILESYSTEM_RENAME'}
for p,t in trees.items():
 if not p.startswith('src/'):continue
 for n in ast.walk(t):
  if not isinstance(n,ast.Call):continue
  raw=ast.unparse(n.func);method=raw.rpartition('.')[2];resource=ast.unparse(n.func.value) if isinstance(n.func,ast.Attribute) else ''
  if method in fs_ops and resource not in ['self','str','text'] and not method=='replace':
   # Lexical API effect is a candidate unless pathlib/os receiver identity is explicit.
   effects.append(dict(entity=p+('::'+scopes[id(n)] if scopes[id(n)] else ''),kind=fs_ops[method],resource=resource,evidence=f'{p}:{n.lineno}',scope='LEXICAL_EFFECT_CANDIDATE'))
  if raw in ['os.replace','os.rename','os.remove','os.unlink','os.fsync','subprocess.run','asyncio.create_subprocess_exec','asyncio.create_subprocess_shell']:
   effects.append(dict(entity=p+('::'+scopes[id(n)] if scopes[id(n)] else ''),kind='SUBPROCESS' if 'subprocess' in raw else 'FILESYSTEM_WRITE',resource=raw,evidence=f'{p}:{n.lineno}',scope='DIRECT_STANDARD_LIBRARY'))
emit('sql_function_edges.csv',sql_edges,['source','target','edge_type','evidence','config_gates','confidence','detail']);emit('state_effects.csv',effects)
for effect in effects:
 if effect['kind'] in {'SQL_READ','SQL_WRITE'}:
  effect['resource']=('sqlite:' if effect['entity'].startswith('src/session/db.py') else 'postgresql:')+effect['resource']
emit('state_effects.csv',effects)
# Transitive SQL summary is separately labeled, never overwrites a direct read/write claim.
a2=collections.defaultdict(set)
for e in E+sql_edges:
 if e['edge_type'] not in {'OUTBOX_PRODUCER','OUTBOX_CONSUMER','BACKGROUND_CONSUMER'}:a2[e['source']].add(e['target'])
by_effect=collections.defaultdict(list)
for v in effects:
 if v['scope']!='LEXICAL_EFFECT_CANDIDATE':by_effect[v['entity']].append(v)
for r in truth:
 r['transitive_data_reads']='';r['transitive_data_writes']='';r['state_effect_basis']=''
 if r['granularity'] not in ['file','symbol']:continue
 start=[r['path']+'::'+r['symbol']] if r['granularity']=='symbol' else [n for n in defs if n.split('::')[0]==r['path']]+[r['path']]
 seen=set(start);q=list(start);found=[]
 while q:
  n=q.pop();found+=by_effect[n]
  for dest in a2[n]:
   if dest not in seen:seen.add(dest);q.append(dest)
 r['transitive_data_reads']=join(v['resource'] for v in found if v['kind']=='SQL_READ');r['transitive_data_writes']=join(v['resource'] for v in found if v['kind']=='SQL_WRITE');r['state_effect_basis']='DIRECT fields remain SQL literals; transitive fields are graph union, path gates apply, mutually exclusive branches may be combined; external/MCP effects not exhausted'
# Keep package rows coherent with updated file classifications.
for r in truth:
 if r['granularity']=='package':
  child=[v for v in truth if v['granularity'] in ['file','asset'] and v['path'].startswith(r['path']+'/')];counts=collections.Counter(v['reachability_class'] for v in child);r['runtime_roots']=join(x for v in child for x in v['runtime_roots'].split(';'));r['unresolved_reason']='混合包，具体分类以文件行为准：'+json.dumps(counts,ensure_ascii=False) if len(counts)>1 else '';r['reachability_class']='UNKNOWN' if len(counts)>1 else next(iter(counts))
# Close only the items whose graph evidence really closed, retain external and structural unknowns.
old_u=read('unresolved_items.csv');u=[];closed=read('resolved_items.csv') if (O/'resolved_items.csv').exists() else []
for item in old_u:
 if item['kind']=='SYMBOL_COVERAGE':
  left=[s for s in item['symbol'].split(';') if any(r['path']==item['path'] and r['symbol']==s and r['reachability_class']=='UNKNOWN' for r in truth)]
  done=sorted(set(item['symbol'].split(';'))-set(left))
  if done:closed.append(dict(item_id=item['item_id'],path=item['path'],closed_symbols=';'.join(done),resolution='concrete binding / framework / registry evidence added',evidence='refined_runtime_edges.csv;composition_bindings.csv'))
  if not left:continue
  item['symbol']=';'.join(left)
 elif item['kind']=='REACHABILITY':
  row=next((r for r in truth if r['path']==item['path'] and r['granularity'] in ['file','asset']),None)
  if row and row['reachability_class']!='UNKNOWN':closed.append(dict(item_id=item['item_id'],path=item['path'],closed_symbols='',resolution='full root-to-file proof now present',evidence=row['proof_id']));continue
 item['evidence_status']='STRUCTURAL_ROLE_KNOWN_RUNTIME_NOT_ASSERTED' if item['path'].endswith('/__init__.py') else 'OPEN';u.append(item)
emit('resolved_items.csv',closed,['item_id','path','closed_symbols','resolution','evidence']);emit('unresolved_items.csv',u);emit('architecture_truth_table.csv',truth);emit('reachability_proofs.csv',proofs)
m=json.loads((O/'audit_manifest.json').read_text());m['runtime_edges']=len(E);m['refinement_runtime_edges']=len(new);m['concrete_receiver_bindings']=len(bindings);m['unknown_rows']=sum(r['reachability_class']=='UNKNOWN' for r in truth);m['unresolved_items']=len(u);m['resolved_item_groups']=len(closed);m['file_reachability_counts']=dict(collections.Counter(r['reachability_class'] for r in truth if r['granularity'] in ['file','asset']));m['all_row_reachability_counts']=dict(collections.Counter(r['reachability_class'] for r in truth));m['fact_flags_top10']=collections.Counter(x for r in truth if r['granularity'] in ['file','asset'] for x in r['fact_flags'].split(';') if x).most_common(10);m['commands'].append({'command':'python3 artifacts/architecture-audit/phase2_1/audit_tools/refine_evidence.py','result':'source-only concrete binding/framework/tool registry and state effect refinement'});m['state_effects']=len(effects);m['sql_function_edges']=len(sql_edges);m['validation']['refinement_integrity']='PENDING';m['output_files']=sorted(str(x.relative_to(O)) for x in O.rglob('*') if x.is_file());m['output_artifact_count']=len(m['output_files']);(O/'audit_manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:m[k] for k in ['runtime_edges','refinement_runtime_edges','concrete_receiver_bindings','unknown_rows','unresolved_items','resolved_item_groups','state_effects','sql_function_edges']},ensure_ascii=False))
