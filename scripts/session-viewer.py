#!/usr/bin/env python3
"""
HpAgent Session Viewer — 本地 Web 可视化工具。

用法:
    python scripts/session-viewer.py              # 默认端口 8090
    python scripts/session-viewer.py --port 9090  # 自定义端口
    python scripts/session-viewer.py --config config/config.yaml  # 指定配置

打开 http://localhost:8090 浏览所有 session 的对话事件。

═══ 数据源 ═══

1. WAL (.wal) — 活跃 session 的运行时真相源（归档后自动删除）
     位置: {backup_dir}/{sid}.wal
     配置: config.yaml → session.backup_dir

2. history.jsonl — 归档真相源，archive 时从 WAL 全量导出
     位置: {workspace_root}/{account}/sessions/{sid}/history.jsonl
     配置: config.yaml → workspace.root

3. meta.yaml — 会话元数据 + fast 模型摘要
     位置: {workspace_root}/{account}/sessions/{sid}/meta.yaml

读取优先级: history.jsonl（归档真相源）→ WAL 兜底（活跃未归档）
══════════════════════════════════════
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml_config(path: Path) -> dict:
    """加载 YAML 配置文件。"""
    try:
        import yaml
    except ImportError:
        print("⚠ yaml 模块未安装，请执行: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    if not path.exists():
        print(f"✗ 配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_path(raw: str, project_root: Path) -> Path:
    """解析相对/绝对路径 → 相对于项目根目录的绝对路径。"""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (project_root / p).resolve()


def _load_paths(config_path: str | None = None) -> tuple[Path, Path]:
    """从 config.yaml 加载 WAL 目录和 workspace 目录。

    Returns:
        (sessions_dir, workspace_dir) — 均为绝对 Path。
    """
    if config_path is None:
        config_path = str(PROJECT_ROOT / "config" / "config.yaml")

    config = _load_yaml_config(Path(config_path))
    project_root = Path(config_path).resolve().parent.parent

    # session.backup_dir → WAL 存储目录（活跃 session）
    session_cfg = config.get("session") or {}
    raw_sessions = session_cfg.get("backup_dir", ".data/active-sessions")
    sessions_dir = _resolve_path(raw_sessions, project_root)

    # workspace.root → 归档存储目录
    workspace_cfg = config.get("workspace") or {}
    raw_workspace = workspace_cfg.get("root", ".data/workspace")
    workspace_dir = _resolve_path(raw_workspace, project_root)

    print(f"配置: {config_path}", file=sys.stderr)
    print(f"  活跃 WAL 目录:  {sessions_dir}", file=sys.stderr)
    print(f"  归档 workspace:  {workspace_dir}", file=sys.stderr)

    return sessions_dir, workspace_dir


def _fmt_ts(ts: float | str) -> str:
    """Unix epoch seconds → 本地时间 24 小时制。"""
    try:
        if isinstance(ts, str):
            ts = float(ts)
        if ts < 1e10:
            pass
        elif ts < 1e13:
            ts = ts / 1000.0
        else:
            ts = ts / 1_000_000.0
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def _load_jsonl(path: Path) -> list[dict]:
    """读取 JSON Lines 文件，每行一个 Event dict。"""
    events = []
    try:
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                events.append(json.loads(line.strip()))
    except Exception:
        pass
    return events


def _load_wal(sessions_dir: Path, sid: str) -> list[dict]:
    """从 WAL 文件读取活跃 session 事件。"""
    return _load_jsonl(sessions_dir / f"{sid}.wal")


def _load_history(user_dir: Path, sid: str) -> list[dict]:
    """从 history.jsonl 读取归档事件。"""
    return _load_jsonl(user_dir / "sessions" / sid / "history.jsonl")


def _load_meta(user_dir: Path, sid: str) -> dict:
    """从 meta.yaml 读取会话元数据。"""
    mf = user_dir / "sessions" / sid / "meta.yaml"
    if mf.exists():
        try:
            import yaml
            return yaml.safe_load(mf.read_text()) or {}
        except Exception:
            pass
    return {}


def _gather_sessions(sessions_dir: Path, workspace_dir: Path) -> dict[str, dict]:
    """扫描 workspace + WAL，聚合为 sessions dict。

    数据源:
      - history.jsonl (.data/workspace/{account}/sessions/{sid}/history.jsonl): 归档真相源
      - WAL (.data/active-sessions/{sid}.wal): 活跃 session 运行时事件（归档后删除）
      - meta.yaml: 会话元数据（含 fast 模型摘要）

    读取优先级: history.jsonl（归档真相源）→ WAL 兜底（活跃未归档）。
    """
    sessions: dict[str, dict] = {}
    archived_sids: set[str] = set()

    # ── 1. workspace history.jsonl + meta.yaml（归档真相源，优先）──
    if workspace_dir.exists():
        for user_dir in sorted(workspace_dir.iterdir()):
            if not user_dir.is_dir():
                continue
            ss = user_dir / "sessions"
            if not ss.is_dir():
                continue
            for sd in sorted(ss.iterdir()):
                if not sd.is_dir():
                    continue
                sid = sd.name
                meta = _load_meta(user_dir, sid)
                history_path = sd / "history.jsonl"

                if history_path.exists():
                    events = _load_history(user_dir, sid)
                    archived_sids.add(sid)
                    sessions[sid] = {
                        "session_id": sid,
                        "source": ["history.jsonl"],
                        "events": events,
                        "meta": meta,
                    }
                    if meta:
                        sessions[sid]["source"].append("meta.yaml")

    # ── 2. WAL（兜底：活跃未归档的 session）──
    if sessions_dir.exists():
        for wf in sorted(sessions_dir.glob("*.wal")):
            sid = wf.stem
            if sid in archived_sids:
                # 已归档但 WAL 尚未删除（极端情况：archive 途中重启），跳过
                continue
            events = _load_wal(sessions_dir, sid)
            if not events:
                continue
            sessions[sid] = {
                "session_id": sid,
                "source": ["wal"],
                "events": events,
                "meta": sessions.get(sid, {}).get("meta", {}),
            }

    # ── 去重 + 排序 ──
    for sid, entry in sessions.items():
        seen = set()
        deduped = []
        for e in entry.get("events", []):
            eid = e.get("event_id")
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            else:
                h = json.dumps(e, sort_keys=True, ensure_ascii=False, default=str)
                if h in seen:
                    continue
                seen.add(h)
            deduped.append(e)
        deduped.sort(
            key=lambda e: e.get("timestamp", 0)
            if isinstance(e.get("timestamp"), (int, float)) else 0
        )
        entry["events"] = deduped

    # ── 过滤幽灵 session ──
    sessions = {sid: s for sid, s in sessions.items() if s.get("events")}

    return sessions


# ═══════════════════════════════════════════════════════════════
# HTML
# ═══════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HpAgent Session Viewer</title>
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--text:#c9d1d9;--dim:#8b949e;--accent:#58a6ff;--user:#3fb950;--model:#58a6ff;--tool:#d29922;--error:#f85149;--memory:#bc8cff;--sys:#8b949e}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.5}
header{background:var(--surface);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;gap:16px}
header h1{font-size:18px;font-weight:600}
header span{color:var(--dim);font-size:13px}
.layout{display:flex;height:calc(100vh - 53px)}
.sidebar{width:340px;min-width:280px;background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;padding:12px}
.sidebar h2{font-size:14px;color:var(--dim);margin-bottom:8px;display:flex;justify-content:space-between}
.sidebar h2 button{font-size:11px;background:var(--border);color:var(--text);border:none;border-radius:4px;padding:2px 8px;cursor:pointer}
.sidebar .search{margin-bottom:8px}
.sidebar .search input{width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px;outline:none}
.sidebar .search input:focus{border-color:var(--accent)}
.card{padding:10px 12px;border:1px solid var(--border);border-radius:6px;margin-bottom:8px;cursor:pointer;transition:.15s}
.card:hover{border-color:var(--accent)}
.card.active{border-color:var(--accent);background:#1a2332}
.card .sid{font-size:11px;color:var(--accent);font-family:monospace;word-break:break-all}
.card .meta{font-size:11px;color:var(--dim);margin-top:3px;line-height:1.4}
.card .summary{font-size:11px;color:var(--text);margin-top:2px}
.main{flex:1;overflow-y:auto;padding:16px 24px}
.event{padding:6px 10px;border-left:3px solid var(--border);margin-bottom:3px;border-radius:0 4px 4px 0;font-size:12px}
.e-user{border-left-color:var(--user);background:#1a2a1f}
.e-model{border-left-color:var(--model);background:#1a2332}
.e-tool{border-left-color:var(--tool);background:#2a2418}
.e-error{border-left-color:var(--error);background:#2a1a1a}
.e-memory{border-left-color:var(--memory);background:#1f1a2a}
.e-system{border-left-color:var(--sys);background:var(--surface)}
.event .hdr{display:flex;align-items:center;gap:6px;margin-bottom:1px}
.event .tag{font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;text-transform:uppercase;flex-shrink:0}
.t-user{background:var(--user);color:#000}
.t-model{background:var(--model);color:#000}
.t-tool{background:var(--tool);color:#000}
.t-error{background:var(--error);color:#fff}
.t-memory{background:var(--memory);color:#000}
.t-system{background:var(--sys);color:#000}
.event .ts{font-size:10px;color:var(--dim);flex-shrink:0}
.event .body{white-space:pre-wrap;word-break:break-word;max-height:300px;overflow-y:auto;padding:4px 6px;background:#00000033;border-radius:3px;font-family:monospace;font-size:11px;margin-top:2px}
.event .body.collapsed{max-height:48px;overflow:hidden;cursor:pointer;position:relative}
.event .body.collapsed::after{content:'▼ 展开';position:absolute;bottom:0;right:0;color:var(--accent);background:linear-gradient(90deg,transparent,var(--bg) 40%);padding:0 6px;font-size:10px}
.tc-list{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
.tc-chip{font-size:10px;color:var(--tool);background:#2a2418;padding:1px 6px;border-radius:3px;font-family:monospace}
.empty{text-align:center;color:var(--dim);padding:60px 20px}
.filter-bar{padding:6px 0;display:flex;gap:5px;flex-wrap:wrap}
.fbtn{font-size:10px;padding:2px 7px;border:1px solid var(--border);background:transparent;color:var(--dim);border-radius:3px;cursor:pointer}
.fbtn.on{border-color:var(--accent);color:var(--accent)}
.stats{font-size:11px;color:var(--dim);margin-bottom:10px;padding:6px 10px;background:var(--surface);border-radius:4px;display:flex;flex-wrap:wrap;gap:8px}
.stats b{font-weight:600}
.meta-box{padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:6px;margin-bottom:12px}
.meta-box h3{font-size:12px;color:var(--dim);margin-bottom:3px}
.meta-box p{font-size:12px;line-height:1.6}
.turn-sep{margin:12px 0 4px;text-align:center;font-size:10px;color:var(--dim);border-top:1px dashed var(--border);padding-top:8px}
</style>
</head>
<body>
<header><h1>HpAgent Session Viewer</h1><span id="scount"></span></header>
<div class="layout">
<aside class="sidebar" id="sb"><div class="search"><input placeholder="搜索 session..." oninput="filterCards(this.value)"></div><div id="slist"></div></aside>
<main class="main" id="main"><div class="empty"><h3>选择一个 Session</h3><p>左侧点击 session ID 查看完整事件流</p></div></main>
</div>
<script>
const API='/api';
let allSessions=[];

async function init(){
  const r=await fetch(API+'/sessions');const d=await r.json();
  allSessions=d.sessions||[];renderList(allSessions);
  document.getElementById('scount').textContent=d.total+' sessions';
}

function renderList(list){
  const c=document.getElementById('slist');
  c.innerHTML=list.length?'':'<div class="empty" style="padding:20px"><p>无 session</p></div>';
  list.sort((a,b)=>(b.first_ts||0)-(a.first_ts||0));
  list.forEach(s=>{
    const el=document.createElement('div');el.className='card';
    el.onclick=()=>load(s.session_id,el);
    const m=s.meta||{};
    const sid=s.session_id.length>50?s.session_id.slice(0,24)+'...'+s.session_id.slice(-20):s.session_id;
    const tags=m.tags&&m.tags.length?m.tags.join(','):'';
    const src=s.source?s.source.join('+').toUpperCase():'';
    el.innerHTML=`<div class="sid">${esc(sid)}</div>
      <div class="meta">${m.created_at?'创建 '+fmt(m.created_at)+' ':''}${m.status||''} 事件:${s.event_count} 来源:${src}</div>
      ${m.task_summary?`<div class="summary">${esc(m.task_summary)}</div>`:''}
      ${tags?`<div class="summary" style="color:var(--tool)">🏷 ${esc(tags)}</div>`:''}`;
    c.appendChild(el);
  });
}

function filterCards(q){
  if(!q){renderList(allSessions);return}
  const kw=q.toLowerCase();
  renderList(allSessions.filter(s=>s.session_id.toLowerCase().includes(kw)||(s.meta?.task_summary||'').includes(kw)||(s.meta?.tags||[]).some(t=>t.includes(kw))));
}

async function load(sid,el){
  document.querySelectorAll('.card').forEach(c=>c.classList.remove('active'));
  if(el)el.classList.add('active');
  const r=await fetch(API+'/session/'+encodeURIComponent(sid));const d=await r.json();
  render(sid,d);
}

function render(sid,d){
  const m=d.meta||{};const evs=d.events||[];
  const cnt={};
  evs.forEach(e=>{const t=etype(e);cnt[t]=cnt[t]?cnt[t]+1:1});

  let h='<div class="meta-box"><h3>Session 元数据</h3><p>';
  h+=`ID: <code style="font-size:11px;word-break:break-all">${esc(sid)}</code><br>`;
  if(m.created_at)h+=`创建: ${fmt(m.created_at)} `;
  if(m.completed_at)h+=`完成: ${fmt(m.completed_at)} `;
  if(m.status)h+=`状态: <b>${esc(m.status)}</b> `;
  if(m.task_summary)h+=`<br>任务: ${esc(m.task_summary)}`;
  if(m.tags&&m.tags.length)h+=`<br>标签: ${esc(m.tags.join(', '))}`;
  if(d.source)h+=`<br>数据源: ${esc(d.source.join(', '))}`;
  h+='</p></div>';

  h+='<div class="stats">';
  const labels={user_message:'用户消息',model_message:'模型回复',tool_result:'工具结果',tool_retrieval:'工具检索',memory_recall:'记忆召回',memory_retain:'记忆保留',memory_reflect:'记忆反思',context_inherit:'上下文继承'};
  Object.entries(cnt).forEach(([k,v])=>{h+=`<span style="color:var(--${colorVar(k)})"><b>${v}</b> ${labels[k]||k}</span>`});
  h+='</div>';

  h+='<div class="filter-bar">';
  [['all','全部'],['user_message','用户消息'],['model_message','模型回复'],['tool_result','工具结果'],['memory','记忆事件'],['system','系统']].forEach(([k,lb])=>{
    h+=`<button class="fbtn${k==='all'?' on':''}" onclick="flt('${k}',this)">${lb}</button>`;
  });
  h+='</div><div id="elist">';
  let lastTurn=-1;
  evs.forEach((e,i)=>{
    // 在 user_message 前插入轮次分隔
    if(etype(e)==='user_message'&&lastTurn>=0){
      h+='<div class="turn-sep">—— 第 '+(lastTurn+2)+' 轮 ——</div>';
    }
    if(etype(e)==='user_message')lastTurn++;
    h+=renderEvent(e,i);
  });
  h+='</div>';
  document.getElementById('main').innerHTML=h;
}

function renderEvent(e,idx){
  const t=etype(e);
  const cls={'user_message':'e-user','model_message':'e-model','tool_result':'e-tool','tool_call':'e-tool','tool_retrieval':'e-tool','error':'e-error'}[t]||(t.startsWith('memory')?'e-memory':'e-system');
  const tc={'user_message':'t-user','model_message':'t-model','tool_result':'t-tool','tool_call':'t-tool','tool_retrieval':'t-tool','error':'t-error'}[t]||(t.startsWith('memory')?'t-memory':'t-system');
  const lb={'user_message':'用户消息','model_message':'模型回复','tool_result':'工具结果','tool_call':'工具调用','tool_retrieval':'工具检索','memory_recall':'记忆召回','memory_retain':'记忆保留','memory_reflect':'记忆反思','context_inherit':'上下文继承','error':'错误'}[t]||t;
  const ts=e.timestamp?fmt(e.timestamp):(e._ts_fmt||'');

  let body='';
  const c=e.content||{};

  if(t==='user_message'){
    body=c.content||'(空)';
  }else if(t==='model_message'){
    body=c.text||'';
    if(c.tool_calls&&c.tool_calls.length){
      body+='\n\n—— 工具调用 ——\n';
      c.tool_calls.forEach(tc=>{
        const args=typeof tc.arguments==='object'?JSON.stringify(tc.arguments):(tc.arguments||'');
        body+=`▶ ${tc.name}(${args.length>200?args.slice(0,200)+'...':args})\n`;
      });
    }
    if(c.stop_reason)body+=`\n[stop: ${c.stop_reason}]`;
    if(c.usage)body+=`\n[tokens: in=${c.usage.in||'?'} out=${c.usage.out||'?'}]`;
  }else if(t==='tool_result'){
    const r=c.result||c.output||'';
    const err=c.error||'';
    const tn=c.tool_name||'';
    body=(tn?`[工具: ${tn}]\n`:'');
    if(err){body+=`❌ ERROR: ${err}`;}
    else{
      body+=typeof r==='string'?r:JSON.stringify(r,null,2);
      const orig=c.original_output||'';
      if(orig&&(c.metadata||{}).summarized){
        body+=`\n\n—— 原始输出 (${orig.length}字符) ——\n`;
        body+=typeof orig==='string'?orig:JSON.stringify(orig,null,2);
      }
    }
  }else if(t==='memory_recall'){
    body=`查询: ${c.query||'?'}\n召回: ${c.items_count||0} 条\n延迟: ${c.latency_ms||0}ms\n标签匹配: ${c.tags_match||''}`;
    if(c.error)body+=`\n错误: ${c.error}`;
    // 展示实际召回的记忆内容
    if(c.items&&c.items.length){
      body+='\n\n—— 召回记忆 ——';
      c.items.forEach((it,i)=>body+=`\n#${i+1} [${it.memory_type||'?'}] (相关度:${(it.relevance||0).toFixed(2)}): ${it.content||''}`);
    }
    if(c.formatted)body+=`\n\n—— 注入 Prompt ——\n${c.formatted}`;
  }else if(t==='memory_retain'){
    body=`存储: ${c.items_stored||0} 条记忆\n来源事件: ${c.events_count||0} 条\n延迟: ${c.latency_ms||0}ms\ndoc: ${c.document_id||''}`;
    if(c.error)body+=`\n错误: ${c.error}`;
    if(c.turn_snippet)body+=`\n\n—— 提交的对话 ——\n${c.turn_snippet}`;
  }else if(t==='memory_reflect'){
    body=`洞察: ${c.insights||0} 条\n延迟: ${c.latency_ms||0}ms`;
    if(c.error)body+=`\n错误: ${c.error}`;
  }else if(t==='context_inherit'){
    body=c.summary||JSON.stringify(c,null,2);
  }else if(t==='tool_retrieval'){
    const limit=c.limit||'?';
    body=`模式: ${c.mode||'?'} | 上限: ${limit}\n查询: ${(c.queries||[]).join(' | ')}\n命中: ${c.tool_count||0} 个工具`;
    if(c.tools&&c.tools.length){
      const scores=c.scores||{};
      body+='\n';
      // 标记 required（始终注入的工具）
      c.tools.forEach((tn,i)=>{
        const sc=scores[tn];
        body+=`\n  ${i+1}. ${tn}${sc!==undefined?' (相关度: '+sc.toFixed(4)+')':''}`;
      });
      body+='\n\n→ 模型实际调用的工具见下方「模型回复」中的"工具调用"';
    }
  }else if(t==='error'){
    body=c.error||c.message||JSON.stringify(c,null,2);
  }else{
    body=typeof c==='object'?JSON.stringify(c,null,2):String(c||'');
  }

  if(typeof body!=='string')body=JSON.stringify(body,null,2);
  const trunc=body.length>500;
  const bcls=trunc?'body collapsed':'body';
  const onclick=trunc?'onclick="this.classList.toggle(\'collapsed\')"':'';

  return `<div class="event ${cls}" data-et="${t.startsWith('memory')?'memory':['user_message','model_message','tool_result','tool_call','tool_retrieval'].includes(t)?t:'system'}">
    <div class="hdr"><span class="tag ${tc}">${lb}</span><span class="ts">${ts||''}</span><span style="font-size:9px;color:var(--dim)">#${idx+1}</span></div>
    <div class="${bcls}" ${onclick}>${esc(body)}</div>
  </div>`;
}

function etype(e){return e.event_type||'';}
function colorVar(t){if(t==='user_message')return'user';if(t==='model_message')return'model';if(t==='tool_result'||t==='tool_call'||t==='tool_retrieval')return'tool';if(t.startsWith('memory'))return'memory';return'system';}

function flt(type,btn){
  document.querySelectorAll('.fbtn').forEach(b=>b.classList.remove('on'));btn.classList.add('on');
  document.querySelectorAll('#elist .event').forEach(el=>{
    if(type==='all'){el.style.display='';return}
    const et=el.dataset.et;
    if(type==='memory'){el.style.display=et.startsWith('memory')?'':'none'}
    else if(type==='system'){el.style.display=!['user_message','model_message','tool_result','tool_call','tool_retrieval'].includes(et)&&!et.startsWith('memory')?'':'none'}
    else{el.style.display=et===type?'':'none'}
  });
}

function fmt(ts){
  if(!ts&&ts!==0)return'';
  let t=typeof ts==='string'?parseFloat(ts):ts;
  if(isNaN(t)||t<=0)return ts+'';
  if(t>1e13)t/=1e6;else if(t>1e10)t/=1000;
  const d=new Date(t*1000);
  if(d.getFullYear()<2000)return ts+''; // invalid
  const p=n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
init();
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    """HTTP 请求处理器 —— sessions_dir / workspace_dir 从类属性注入。"""

    sessions_dir: Path = PROJECT_ROOT / ".data" / "active-sessions"
    workspace_dir: Path = PROJECT_ROOT / ".data" / "workspace"

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._html()
        elif p == "/api/sessions":
            self._json(self._list())
        elif p.startswith("/api/session/"):
            self._json(self._get(p[len("/api/session/"):]))
        else:
            self.send_response(404); self.end_headers()

    def _html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False, default=str)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _list(self):
        sessions = _gather_sessions(self.sessions_dir, self.workspace_dir)
        result = []
        for sid, s in sessions.items():
            m = s.get("meta", {})
            evts = s.get("events", [])
            first_ts = evts[0].get("timestamp") if evts else None
            result.append({
                "session_id": sid,
                "meta": {
                    "created_at": m.get("created_at", ""),
                    "completed_at": m.get("completed_at", ""),
                    "status": m.get("status", ""),
                    "task_summary": m.get("task_summary", ""),
                    "tags": m.get("tags", []),
                },
                "event_count": len(evts),
                "source": list(set(s.get("source", []))),
                "first_ts": first_ts,
            })
        result.sort(key=lambda s: s.get("first_ts") or 0, reverse=True)
        return {"total": len(result), "sessions": result}

    def _get(self, sid):
        sessions = _gather_sessions(self.sessions_dir, self.workspace_dir)
        s = sessions.get(sid, {})
        for e in s.get("events", []):
            ts = e.get("timestamp")
            if ts:
                e["_ts_fmt"] = _fmt_ts(ts)
        return {
            "session_id": sid,
            "meta": s.get("meta", {}),
            "source": list(set(s.get("source", []))),
            "events": s.get("events", []),
        }

    def log_message(self, fmt, *args):
        print(f"  {args[0]}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="HpAgent Session Viewer")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--config", default=None,
                        help="config.yaml 路径 (默认: config/config.yaml)")
    args = parser.parse_args()

    # 检查 yaml 是否可用
    try:
        import yaml as _  # noqa
    except ImportError:
        print("⚠ yaml 模块未安装，将无法解析 meta.yaml")
        print("  安装: pip install pyyaml")

    # 从配置文件加载路径
    sessions_dir, workspace_dir = _load_paths(args.config)

    # 确保目录存在
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # 注入到 Handler 类属性
    handler_cls = type("SessionViewerHandler", (Handler,), {
        "sessions_dir": sessions_dir,
        "workspace_dir": workspace_dir,
    })

    print(f"Session Viewer → http://{args.host}:{args.port}", file=sys.stderr)
    HTTPServer((args.host, args.port), handler_cls).serve_forever()


if __name__ == "__main__":
    main()
