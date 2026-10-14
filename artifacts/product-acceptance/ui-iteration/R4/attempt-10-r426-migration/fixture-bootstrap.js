
/* R4 explicit browser fixtures. All non-auth writes are blocked; production React is unchanged. */
const data = {
  "seed": {
    "source_kind": "chat",
    "run": {
      "source_kind": "chat",
      "run_id": "01a13187-fcae-7571-b501-64bad4e26345",
      "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
      "session_id": null,
      "trigger_message_id": "01a13187-fcad-716d-8420-39ed356ce4f8",
      "retry_of_run_id": null,
      "agent_strategy": "react",
      "status": "succeeded",
      "failure": null,
      "version": 3,
      "created_at": "2026-10-12T19:12:38.053Z",
      "started_at": "2026-10-12T19:12:38.123Z",
      "finished_at": "2026-10-12T19:12:40.751Z",
      "updated_at": "2026-10-12T19:12:40.751Z",
      "budget": {
        "status": "ok",
        "mode": "observe",
        "policy_version": "file-p0-v1",
        "tokens": {
          "input": {
            "used": 0,
            "reserved": 0,
            "limit": 80000
          },
          "output": {
            "used": 0,
            "reserved": 0,
            "limit": 20000
          },
          "total": {
            "used": 0,
            "reserved": 0,
            "limit": 100000
          }
        },
        "model_calls": {
          "settled": 0,
          "in_flight": 0,
          "unmetered": 0,
          "total_attempts": 0,
          "limit": 20
        },
        "by_source": {
          "provider": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
          },
          "measured": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
          },
          "estimated": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
          }
        },
        "usage_state": "none",
        "usage_quality": "none",
        "has_estimates": false,
        "model_total_tokens_used": 0,
        "model_total_tokens_limit": 100000,
        "tool_calls_used": 0,
        "tool_calls_limit": 20,
        "bytes_scanned_used": 0,
        "bytes_scanned_limit": 536870912
      },
      "execution_id": "36f01fd8-01ed-37f0-9126-72e2ce1e45cc"
    },
    "assistant_message": {
      "message_id": "01a13187-fcaf-7bf5-8129-129e1647e904",
      "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
      "role": "assistant",
      "status": "completed",
      "content": "已整理三项评审重点：正文阅读、成果身份与执行证据。下方的 HTML 成果可直接打开，查看后可以继续在当前对话中修改。",
      "sequence": 2,
      "client_request_id": null,
      "produced_by_run_id": "01a13187-fcae-7571-b501-64bad4e26345",
      "created_at": "2026-10-12T19:12:38.053Z",
      "completed_at": "2026-10-12T19:12:40.751Z",
      "files": []
    }
  },
  "detail": {
    "conversation": {
      "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
      "title": "项目评审 · 阅读与交付",
      "status": "active",
      "last_message_seq": 4,
      "metadata_version": 1,
      "created_at": "2026-10-12T19:12:37.986Z",
      "updated_at": "2026-10-13T11:04:45.909Z"
    },
    "active_run": null
  },
  "messagePage": {
    "items": [
      {
        "message_id": "01a13187-fcad-716d-8420-39ed356ce4f8",
        "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
        "role": "user",
        "status": "accepted",
        "content": "请整理项目评审重点，并准备一份可打开的 HTML 成果。",
        "sequence": 1,
        "client_request_id": "88842283-f9ea-470b-9c1e-db910a3ecc28",
        "produced_by_run_id": null,
        "created_at": "2026-10-12T19:12:38.053Z",
        "completed_at": null,
        "files": []
      },
      {
        "message_id": "01a13187-fcaf-7bf5-8129-129e1647e904",
        "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
        "role": "assistant",
        "status": "completed",
        "content": "已整理三项评审重点：正文阅读、成果身份与执行证据。下方的 HTML 成果可直接打开，查看后可以继续在当前对话中修改。",
        "sequence": 2,
        "client_request_id": null,
        "produced_by_run_id": "01a13187-fcae-7571-b501-64bad4e26345",
        "created_at": "2026-10-12T19:12:38.053Z",
        "completed_at": "2026-10-12T19:12:40.751Z",
        "files": []
      },
      {
        "message_id": "01a134ef-b05c-75ec-a077-9796168870bc",
        "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
        "role": "user",
        "status": "accepted",
        "content": "saddasd",
        "sequence": 3,
        "client_request_id": "854567b8-207f-44ae-97f0-85d9f5eedaa5",
        "produced_by_run_id": null,
        "created_at": "2026-10-13T11:04:45.909Z",
        "completed_at": null,
        "files": []
      },
      {
        "message_id": "01a134ef-b05e-7508-ab75-8c748c09c508",
        "conversation_id": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
        "role": "assistant",
        "status": "completed",
        "content": "这是由测试执行器生成的回复。",
        "sequence": 4,
        "client_request_id": null,
        "produced_by_run_id": "01a134ef-b05d-796f-8e5c-f3f6d8cdf60b",
        "created_at": "2026-10-13T11:04:45.909Z",
        "completed_at": "2026-10-13T11:04:48.585Z",
        "files": []
      }
    ],
    "next_cursor": null,
    "has_more": false,
    "conversation_last_message_seq": 4
  },
  "conversationId": "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
  "runId": "01a13187-fcae-7571-b501-64bad4e26345"
}, nativeFetch = window.fetch.bind(window);
const params = new URLSearchParams(location.search), scene = params.get('case') || 'normal';
window.__reviewClient = crypto.randomUUID?.() || "standalone-review";
window.__r4events = [];
window.addEventListener('hp-inspector-relay-event', event => {
 let routeId=new URLSearchParams(location.hash.split('?')[1]||'').get('inspect')?.replace(/^run:/,'');
 if(routeId)window.__r4lastRoute=routeId;else routeId=window.__r4lastRoute;
 const nodes={source:document.getElementById('run-open-'+routeId),target:document.querySelector('.hp-inspector-receiver')};
 const inspect=node=>{if(!node)return {exists:false};const r=node.getBoundingClientRect(),style=getComputedStyle(node),blocked=node.closest('[hidden],[inert]'),dialog=node.closest('dialog');return {exists:true,id:node.id,connected:node.isConnected,rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},visibility:style.visibility,display:style.display,blocked:blocked?{tag:blocked.tagName,hidden:blocked.hidden,inert:blocked.inert,className:blocked.className}:null,dialog:dialog?{open:dialog.open,modal:dialog.getAttribute('aria-modal'),className:dialog.className}:null};};
 const item={reviewClient:window.__reviewClient,automated:navigator.webdriver,...event.detail, viewport:innerWidth+'x'+innerHeight, motion:document.querySelector('.hp-shell')?.dataset.spatialMotion,eligibility:{hidden:document.hidden,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,source:inspect(nodes.source),target:inspect(nodes.target),modals:[...document.querySelectorAll('dialog[open]:not(.hp-surface--region)')].map(n=>({className:n.className,hidden:n.hidden,inert:n.inert}))}};
 window.__r4events.push(item);
 // Standalone: diagnostics stay in memory; no server logging.
});
window.__r4log = []; window.__r4scene = scene;
const log = (type, extra={}) => window.__r4log.push({t:performance.now(),type,...extra});
const json = (body,status=200) => new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const ids = ['r4-A','r4-B','r4-C'];
const snapshots = Object.fromEntries(ids.map((id,i)=>[id,{...structuredClone(data.seed),run:{...data.seed.run,run_id:id,status:'succeeded'},assistant_message:{...data.seed.assistant_message,message_id:'r4-assistant-'+i,produced_by_run_id:id,content:['执行 A：第一条已核实的结果。','执行 B：第二条结果，独立来源。','执行 C：最后选择的当前结果。'][i],status:'completed'}}]));
const user = {...data.messagePage.items.find(m=>m.role==='user'),message_id:'r4-user',sequence:1,content:'检查这三次执行的来源、抵达和关闭连续性。'};
const messages = [user,...ids.map((id,i)=>({...snapshots[id].assistant_message,sequence:2+i}))];
let sink, sent = 0;
window.__r4setState = status => { if (!sink) return; const s=snapshots['r4-send']; s.run.status=status;const event={schema_version:1,event_id:'r4-state-'+status,event_type:'run.snapshot',conversation_id:data.conversationId,run_id:s.run.run_id,message_id:s.assistant_message.message_id,stream_id:null,event_seq:null,occurred_at:new Date().toISOString(),payload:{snapshot:s}};sink.enqueue(new TextEncoder().encode(`id: ${event.event_id}\nevent: run.snapshot\ndata: ${JSON.stringify(event)}\n\n`)); };
window.__r4progress = phase => {if(!sink)return; const s=snapshots['r4-send'];const event={schema_version:1,event_id:'r4-progress-'+phase,event_type:'run.progress',conversation_id:data.conversationId,run_id:s.run.run_id,message_id:s.assistant_message.message_id,stream_id:'r4-progress',event_seq:1,occurred_at:new Date().toISOString(),payload:{phase,summary:'明确夹具：快速阶段'}};sink.enqueue(new TextEncoder().encode(`id: ${event.event_id}\nevent: run.progress\ndata: ${JSON.stringify(event)}\n\n`));};
window.fetch = async (input,init) => {
 const url = new URL(typeof input==='string'?input:input.url,location.href), method=init?.method||'GET';
 if(url.pathname==='/api/v1/me')return json({account:{account_id:'r4-fixture-account',status:'active',created_at:'2026-10-14T00:00:00Z'},session:{expires_at:'2099-01-01T00:00:00Z',idle_expires_at:'2099-01-01T00:00:00Z'},csrf_token:'explicit-browser-fixture',identities:{web:{username:'R4 明确评审夹具'},qq:{bound:false}},capabilities:{agent_strategies:['react','plan_and_execute'],durable_agent:true,file_upload:false,qq_self_service_binding:false}});
 if(url.pathname===`/api/v1/conversations/${data.conversationId}/resources`)return json({grants:[],attachments:[]});
 if(url.pathname.startsWith('/auth/'))return json({error:{code:'fixture_no_real_login',message:'明确评审夹具不执行真实登录。'}},409);
 log('request',{path:url.pathname,method});
 if(method==='POST' && url.pathname.endsWith('/messages')) {
  const id='r4-send',now=new Date().toISOString();sent++;
  const assistant={...data.seed.assistant_message,message_id:'r4-sent-'+sent,produced_by_run_id:id,content:'',sequence:10+sent*2,status:'pending'};
  const run={...data.seed.run,run_id:id,status:'queued',failure:null,version:1,created_at:now,updated_at:now,finished_at:null};
  snapshots[id]={source_kind:'chat',run,assistant_message:assistant};
  const u={...user,message_id:'r4-sent-user-'+sent,sequence:9+sent*2,content:JSON.parse(init.body).content};messages.push(u,assistant);
  return json({user_message:u,assistant_message:assistant,run});
 }
 if(method==='POST' && url.pathname.endsWith('/cancel')) {window.__r4setState('cancelled');return json(snapshots['r4-send']);}
 if(method!=='GET') return json({error:{code:'fixture_readonly',message:'明确评审夹具：其余写入被阻止。'}},409);
 if(url.pathname==='/api/v1/conversations')return json({items:[{...data.detail,conversation_id:data.conversationId,title:'R4 Run 纵向闭环'}],next_cursor:null});
 if(url.pathname===`/api/v1/conversations/${data.conversationId}`)return json({...data.detail,active_run:null});
 if(url.pathname.endsWith('/messages'))return json({...data.messagePage,items:messages});
 const match=url.pathname.match(/^\/api\/v1\/runs\/([^/]+)$/);
 if(match && snapshots[match[1]]) {
  const id=match[1]; const delay=scene==='slow'?1800:scene==='fast-switch'?id==='r4-A'?1600:id==='r4-B'?1100:60:40;
  await new Promise(r=>setTimeout(r,delay));
  log('response',{id,delay});
  if(scene==='denied')return json({error:{code:'not_found',message:'对象不可用。'}},403);
  if(scene==='offline')throw new TypeError('explicit fixture offline');
  return json(snapshots[id]);
 }
 if(url.pathname.endsWith('/events')) return new Response(new ReadableStream({start(c){sink=c; init.signal?.addEventListener('abort',()=>{if(sink===c)sink=null;c.close()},{once:true});},cancel(){sink=null;}}),{headers:{'Content-Type':'text/event-stream'}});
 if(/\/runs\/[^/]+\//.test(url.pathname))return json(url.pathname.endsWith('/file-action-approvals')?{approvals:[]}:url.pathname.endsWith('/resources')?{candidates:[],next:null}:url.pathname.endsWith('/published-files')?{files:[]}:url.pathname.endsWith('/model-inputs')?{visibility:'none',items:[]}:{items:[],next_cursor:null});
 if(url.pathname.endsWith('/artifacts'))return json({items:[],next_cursor:null});
 return json({error:{code:"fixture_out_of_scope",message:"此请求不在Run评审夹具范围内。"}},403);
};
location.hash='#/ai/'+data.conversationId;
const start=performance.now();
function sample(){const receiver=document.querySelector('[data-inspector-phase]'),relay=document.querySelector('.hp-inspector-relay');log('frame',{phase:receiver?.dataset.inspectorPhase,head:relay?.dataset.tipLength,length:relay?.dataset.totalLength,token:relay?.dataset.token,title:document.getElementById('inspector-title')?.textContent,titleArrival:document.getElementById('inspector-run-title')?.classList.contains('hp-inspector-title-arrival'),detailsVisible:!!document.querySelector('[data-inspector-gate]:not([hidden])'),titleOpacity:document.getElementById('inspector-title')?getComputedStyle(document.getElementById('inspector-title')).opacity:null,focus:document.activeElement?.id||document.activeElement?.tagName,hash:location.hash,exit:!!document.querySelector('.hp-inspector-exit')});if(performance.now()-start<120000)requestAnimationFrame(sample);}
requestAnimationFrame(sample);

const fontChoice = Number(params.get("completeSize"));
if ([12,14,16,18].includes(fontChoice)) document.documentElement.style.setProperty("--hp-run-complete-size", fontChoice+"px");

