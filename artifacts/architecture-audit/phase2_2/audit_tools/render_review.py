"""Render R2.1 audit registers. Writes only within phase2_2; never imports product code."""
from pathlib import Path
import csv
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'artifacts/architecture-audit/phase2_2'
OLD = OUT.parent / 'phase2_1'
REVISION = '2.2-r2.1'
BANNER = '> Historical architecture evidence. Not current architecture documentation.\n> 修订 R2.1；所有目标改造 NOT IMPLEMENTED，代码事实与目标分列。\n\n'


def read_csv(path):
    with path.open(encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def write_csv(name, rows):
    with (OUT / name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def write_text(name, text):
    (OUT / name).write_text(text.rstrip() + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def related(path):
    if path.startswith('src/agent/'):
        return 'ACD-03;ACD-10', 'G03;G05'
    if path.startswith('src/account/'):
        return 'ACD-04;ACD-10', 'G05;G06'
    if path.startswith('src/channels/'):
        return 'ACD-01;ACD-08', 'G03;G06'
    if path.startswith('src/sandbox/'):
        return 'ACD-11;ACD-17', 'G09;G12'
    if path.startswith(('src/file_', 'src/document_')):
        return 'ACD-05;ACD-18', 'G10;G13'
    if path.startswith(('src/agent_activities/', 'src/agent_workflows/')):
        return 'ACD-01;ACD-17', 'G04;G12'
    if path.startswith('src/agent_execution/'):
        return 'ACD-01;ACD-09;ACD-17', 'G03;G05;G12'
    if path.startswith('src/research_'):
        return 'ACD-06;ACD-07', 'G04;G10'
    if path.startswith('src/orchestration/'):
        return 'ACD-01;ACD-02;ACD-09', 'G02;G04;G05'
    if path.startswith('src/web_api/'):
        return 'ACD-04;ACD-15;ACD-18', 'G06;G13'
    if path.startswith('src/web_artifacts/'):
        return 'ACD-07', 'G10'
    if path.startswith(('src/workspace/', 'src/session/')):
        return 'ACD-04;ACD-16;ACD-18', 'G06;G07;G13'
    return 'ACD-04', 'G03;G06'


manifest = json.loads((OUT / 'audit_manifest.json').read_text())
# Refuse to silently bless changed inputs. R1 already froze all non-output tracked files.
for group in ('phase2_1_input_sha256', 'protected_tracked_file_sha256'):
    for path, digest in manifest[group].items():
        assert (ROOT / path).is_file() and sha(ROOT / path) == digest, path
if manifest.get('revision') != REVISION:
    manifest['parent_review'] = {'revision': manifest.get('revision'), 'review_head': manifest['review_head'], 'artifact_commit': git('rev-parse', 'HEAD')}
    manifest.setdefault('original_review', {
        'head': manifest['review_head'],
        'phase2_2_original_commit': '4c18f5dff854c31bca1822028eb173bcb6a6055f',
        'status': 'SUPERSEDED_BY_R2_USER_PREMISE',
    })
    manifest['review_head'] = git('rev-parse', 'HEAD')
    manifest['outside_scope_diff_at_revision_start'] = git(
        'diff', '--binary', 'HEAD', '--', '.',
        ':(exclude)artifacts/architecture-audit/phase2_2')
    manifest['outside_scope_untracked_at_revision_start'] = git(
        'ls-files', '--others', '--exclude-standard', '--', '.',
        ':(exclude)artifacts/architecture-audit/phase2_2').splitlines()

register = json.loads((OUT / 'decision_register.json').read_text())
decisions = register['decisions']
rows = []
sections = []
for d in decisions:
    row = {'revision': REVISION, 'classification': 'TARGET_DECISION',
           'implementation_status': 'NOT_IMPLEMENTED'}
    row.update({k: ';'.join(v) if isinstance(v, list) else v for k, v in d.items()})
    rows.append(row)
    sections.append(
        f"## {d['decision_id']} · {d['title']}\n\n"
        f"| 项目 | 记录 |\n| --- | --- |\n| 修订 | {d['change_type']} / {d['priority']} |\n"
        f"| 决策依据 | {d['authority']} |\n| 实施状态 | NOT IMPLEMENTED |\n"
        f"| 源码事实 | {';'.join(d['evidence_ids'])}；{'；'.join(d['hypothesis_ids'])} |\n"
        f"| 门禁 | {';'.join(d['gate_ids'])}（见 03 的 R2.1 定义） |\n\n"
        f"**CURRENT FACT / Current：** {d['current_fact']}\n\n"
        f"**TARGET DECISION / Target：** {d['target_decision']}\n\n"
        f"**Required migration/refactor：** {d['required_refactor']}\n\n"
        f"**FUTURE OPTION：** {d['future_option']}\n\n"
        f"**理由：** {d['rationale']}\n\n**取舍：** {d['tradeoff']}\n\n"
        f"**验证入口（未执行；旧合同可按目标替换）：** {'；'.join('`'+p+'`' for p in d['validation_refs'])}。\n\n"
        f"**后续回退：** {d['rollback']}"
    )
write_csv('decision_register.csv', rows)
write_text('01_decision_register.md', '# 架构决策登记册 · R2.1\n\n' + BANNER +
           '用户指定的 canonical 方向已经明确；具体命名/合同/busy 策略等为审计建议。'
           'REVERSED 表示推翻原结论；REVISED 表示依新目标重写；RETAINED_AND_RESCOPED 表示保留方向并调整边界；NEW 表示新增。'
           'R2 已重审原 16 项并新增 17/18；R2.1 只修订 01/04/17 及相关合同。本轮不采纳任何“已经完成代码改造”的状态。\n\n' + '\n\n'.join(sections))

# Keep original source anchors; append only inspected source, never turn target nouns into code facts.
evidence = [r for r in read_csv(OUT / 'evidence_register.csv') if int(r['evidence_id'][1:]) <= 25]
specs = [
    ('E44', '当前一次性执行 lease', 'src/orchestration/durable_web_workflow.py', 'class DurableWebRunWorkflow', '启动 AgentRunWorkflow 前 acquire，固定 token 传入子 Workflow，finally 用同 token release；suspend-safe 分段 lease 是 W1 目标，未实现。'),
    ('E45', '当前 Agent 输入硬绑定', 'src/agent_workflows/contracts.py', 'class AgentRunInput', '必填 conversation_id/session_id 与固定 lease_token；source-neutral 输入及可更新执行 token 是目标，未实现。'),
    ('E26', 'QQ account 级会话入口', 'src/application/conversation.py', '    async def start_or_signal', '当前按 account start/signal OrchestrationWorkflow，生成字符串 session_id，并准备本地资源。'),
    ('E27', 'PG 交互实体与单活跃约束', 'persistence/migrations/001_phase_a_schema.sql', 'CREATE TABLE conversations', 'Conversation/Message/Session/Run 及 queued/running/cancelling 单活跃索引已存在，未含目标 QQ 适配。'),
    ('E28', 'Web 交互事务与 busy', 'src/web_domain/services.py', '    def send_message', '先锁 Conversation、查幂等/busy，再建 Session/Message/Run/budget/Outbox；不是已实现中立 QQ 命令。'),
    ('E29', '准备与模型调用耦合', 'src/agent_activities/runtime.py', '    async def model_decision', '同 Activity 读 transcript、加 objective、选工具并调模型；没有预调用审阅。'),
    ('E30', '调用后输入投影', 'src/brain/engine.py', '    def snapshot_context', 'generate_chat_decision 在模型返回后生成 input_context；不是完整 provider 请求快照。'),
    ('E31', '真正 provider payload 转换', 'src/resources/model_client.py', '    def _build_payload', '选择实际 model，转换 messages/tools、填有效 max_tokens/stream 并合并 extra_body。'),
    ('E32', '模型回退发生于运行时', 'src/resources/resource_pool.py', '    async def generate', 'selector 解析候选 endpoint，按 attempt 调 client 并做预算 reserve/settle，可能 fallback。'),
    ('E33', '文件审批的 durable wait', 'src/agent_workflows/tool_execution.py', 'class ToolExecutionWorkflow', 'signal 唤醒后查询权威审批状态，wait_condition 等待；当前是工具/文件审批而非 Model Review。'),
    ('E34', '执行 lease 过期与 fencing', 'src/agent_activities/store.py', '    def validate_and_renew_lease', '只有 owner/token/未过期符合条件才续租，不能把长时间审阅后的旧 token 当作自动有效。'),
    ('E35', 'Research Run 非聊天 shape', 'persistence/migrations/021_research_r0_r2.sql', 'ALTER TABLE runs ADD COLUMN run_kind', 'Research Run 绑定 Task，conversation/session/trigger/context_seq 为空；同表共享 Run 不等于共享聊天实体。'),
    ('E36', '当前 checkout 不是任意会话快照', 'src/workspace/isolation.py', '    async def lease_for_run', '加载 account_repo/session 后锁账户、准备并切分支，还写 Web session_context；执行准备不能用作只读查询。'),
    ('E37', '上下文准备已有辅助模型调用', 'src/agent_activities/runtime.py', '    async def context_bootstrap', 'loader/记忆组装写 transcript；可先调用 fast 模型改写 recall query；trace surface 当前写 web。'),
    ('E38', 'QQ SessionStore 仍实际存在', 'src/session/store.py', 'class SessionStore', 'QQ Redis/WAL/checkpoint 现状；目标统一 PG 需要迁移正式消费者，不能以改文档声称已删除。'),
    ('E39', 'Run Files 独立范围', 'src/workspace/file_scope.py', 'class RunFileWorkspace', 'Run 文件输入/临时/输出范围，区别于 Git account/session workspace。'),
    ('E40', 'harness 含混合必要能力', 'src/harness/activities.py', 'async def process_turn_activity', 'QQ turn 调旧 Host；同文件还有 archive/reflection/metrics，不能按目录整体判可删。'),
    ('E41', '规划与评估另有模型调用', 'src/agent_activities/runtime.py', '    async def planning', 'planning/evaluate_plan 构建专门 instructions 后直接调用 Brain；只拆 model_decision 无法覆盖全部主模型阶段。'),
    ('E42', '当前文档维护约束', 'docs/architecture/README.md', '## 事实优先级', '要求实现事实优先、显式 drift 与 Excalidraw 人工维护；目标 ADR/当前文档分工是本次建议。'),
    ('E43', '当前审批 UI 范围', 'web/src/components/ApprovalCard.tsx', 'export function ApprovalCard', '调用 listFileApprovals/decideFileApproval 并展示文件目的地；不是模型输入权限投影。'),
]
for eid, title, path, needle, claim in specs:
    lines = (ROOT / path).read_text().splitlines()
    line = next(i for i, text in enumerate(lines, 1) if needle in text)
    evidence.append(dict(evidence_id=eid, title=title, path=path, line=line,
                         claim=claim, kind='SOURCE_REVIEW', sha256=sha(ROOT / path)))
evidence.sort(key=lambda row: row['evidence_id'])
for e in evidence:
    e['classification'] = 'CURRENT_FACT'
    e['revision'] = REVISION
write_csv('evidence_register.csv', evidence)
write_text('05_evidence_index.md', '# 代码事实证据索引 · R2.1\n\n' + BANNER +
    'E01–E25 继承原锚点且哈希核对；E26–E43 继承 R2；R2.1 定向补充 E44/E45，未重审全部 ACD。这里证明当前实现/差距，'
    '不能证明目标已实现。未生产前提来自用户 U-PREPROD，不来自 E 表。测试源码均只读未执行。\n\n'
    '| ID | 复核项 | 源码位置 | CURRENT FACT |\n| --- | --- | --- | --- |\n' +
    '\n'.join(f"| {e['evidence_id']} | {e['title']} | `{e['path']}:{e['line']}` | {e['claim']} |" for e in evidence) +
    '\n\n新功能负证据：对 src 与 web/src 搜索 ModelInputSnapshot、WorkspaceQueryService、'
    'prepare_model_input、invoke_model、prompt_review 未命中，再结合模型调用与 API/ApprovalCard 实现复核。'
    '这说明本仓库缺少上述目标合同，不声称没有任何相邻能力。Blackboard 未检出独立同名实现，'
    '退役按实验类真实消费者处理，不捏造 blackboard.py。\n\n'
    'E24 结合 Compose 的实际 build context 与四份 Dockerfile；E22/E23 的旧 replay/双注册测试只描述旧合同，'
    '新目标允许在行为验证后替换。代码基线变化后应重新检查适用性；validator 接受仅审计提交导致 HEAD 改变，'
    '但拒绝冻结证据或范围外源文件漂移。\n\n'
    '未读线上 History、真实业务数据库、私有部署配置；这些不再是 legacy 删除前置。'
    '目标 PG/Temporal E2E、MCP transport、workspace 隔离、模型输入一致性仍须在实施时验证。')

hmap = {
 'H01': ('ACD-03;ACD-08;ACD-10', '正式协议先救出，非目标实验/配置后删除；不再永久保留实验包'),
 'H02': ('ACD-04;ACD-10', 'PG 统一身份，旧 JSON 代码经目标消费者核验删除；不需旧生产数据迁移'),
 'H03': ('ACD-01;ACD-08;ACD-09', '双启动/双注册是当前过渡事实；W1 先冻结 source-neutral 与 suspend-safe 合同，目标验证后删除'),
 'H04': ('ACD-03;ACD-09', 'QQ 迁入 canonical 后退役旧 loop；harness 必要 context/记忆/定时能力先迁出'),
 'H05': ('ACD-02', '唯一主线和 legacy 清理后整理 composition，不先搬旧双栈'),
 'H06': ('ACD-11;ACD-12;ACD-17', '按能力拆分，模型 preparation/invocation 成为明确切口'),
 'H07': ('ACD-06;ACD-15', 'Research 固定且独立；共享 Run 不强制 Conversation；UI 另排'),
 'H08': ('ACD-05;ACD-16;ACD-18', '保留 File/Document 语义与独立 Activity；workspace query 分 scope'),
 'H09': ('ACD-07', '结果资产合同统一，Research 原子发布桥与聊天 build 保留'),
 'H10': ('ACD-02;ACD-06;ACD-09', '提醒/反思等必要服务从旧 turn 依赖分离，不并入 Research'),
 'H11': ('ACD-16;ACD-18', '当前未实现 worktree，明确拒绝；作为未来隔离候选而非无需求'),
 'H12': ('ACD-08', '保留开发 fake gate；不能以清理 migration 为由删除全部 feature gates'),
 'H13': ('ACD-13', '维护源唯一，保留必要 build 输入或后续统一上下文'),
 'H14': ('ACD-04;ACD-16;ACD-18', 'Conversation/Message/Session 归 Conversation，Run 为共享 Execution/Lifecycle；PG admission 唯一，busy 为可替换 policy；不同 workspace/file/memory 对象继续分工'),
 'H15': ('ACD-01;ACD-11;ACD-17', '统一 Agent，保留 Model/Tool；每个 snapshot 满足冻结 AuthorizationPolicy，人工频率可替换；W1 suspend/resume，W5 接入审阅'),
 'H16': ('ACD-08', '渠道默认与支持项对齐；目标无 legacy 编排选项'),
 'H17': ('ACD-10;ACD-13', '保留全新 schema 创建，删除 obsolete compatibility 不能按目录名判断'),
}
hrows = [dict(hypothesis_id=r['id'], phase2_1_status=r['status'],
              decision_ids=hmap[r['id']][0], disposition=hmap[r['id']][1],
              fact_status='UNCHANGED_CURRENT_FACT', implementation_status='NOT_IMPLEMENTED')
         for r in read_csv(OLD / 'hypothesis_results.csv')]
write_csv('hypothesis_decisions.csv', hrows)
dmap = {
 'D01': ('ACD-01;ACD-14', 'HEAD 仍双路径；目标单 durable，当前文档纠事实、ADR 写目标，随实现再收口'),
 'D02': ('ACD-05;ACD-14;ACD-16', 'HEAD 容器清单补真实 Document/依赖；目标部署仍不冒称 worktree 已实现'),
 'D03': ('ACD-03;ACD-10;ACD-14', '当前协议正式使用必须记录；未来先迁协议再删除实验目录，旧 closure 可由 Git/ADR 保存'),
 'D04': ('ACD-01;ACD-09;ACD-14', '当前时序补 durable；目标 Web/QQ 单主线；旧稿后续移除或保留重要 ADR'),
 'D05': ('ACD-08', '按真实渠道能力修合同；目标删除迁移开关，不扩展 Console 实现'),
 'D06': ('ACD-09;ACD-12', '旧 Activity 当前含执行 Host；目标删 execute_agent，保留并中立化必要 lifecycle'),
 'D07': ('ACD-04;ACD-09', '当前 SessionStore 消费链保留为事实，目标迁 PG 后删旧权威，不永久只改注释'),
 'D08': ('ACD-05;ACD-16', '沿用 2.1 已纠正的 Document Workflow/Activity 边界'),
 'D09': ('ACD-07', '沿用 2.1 两发布路径事实，统一资产合同而保留业务流程'),
 'D10': ('ACD-03;ACD-08;ACD-10', '当前存在即解析 agents.yaml；目标移除非目标实验加载/配置'),
}
drows = [dict(drift_id=r['id'], decision_ids=dmap[r['id']][0],
              disposition=dmap[r['id']][1], status='FACT_CORRECTED_IN_PHASE2_1' if r['id'] in ('D08','D09') else 'TARGET_REVISED_NOT_IMPLEMENTED')
         for r in read_csv(OLD / 'architecture_drift.csv')]
write_csv('drift_dispositions.csv', drows)
external = {
 'U-DEPLOY': ('ACD-08;ACD-09;ACD-16', 'G05;G11', 'PREMISE_SUPERSEDED', '无需旧部署盘点/兼容；仍需验证目标配置和全新启动'),
 'U-HISTORY': ('ACD-09', 'G04;G05', 'PREMISE_SUPERSEDED', '无需旧线上 History 存量；验证 canonical replay/recovery，不能伪称已查线上为零'),
 'U-ACCOUNTS': ('ACD-04;ACD-10', 'G05;G06', 'PREMISE_SUPERSEDED', '无需旧生产账号回填；核验目标身份路径与拟删源码消费者'),
 'U-TOPOLOGY': ('ACD-16;ACD-18', 'G07;G13', 'FUTURE_OPTION_PENDING', 'worktree 当前未实现；未来开放需隔离证明，Query 可先用真实可用视图'),
 'U-PLUGINS': ('ACD-11;ACD-17', 'G09;G12', 'TARGET_PROOF_PENDING', '验证所改 transport 和工具定义冻结，不要求所有远端工具清单'),
 'U-RESEARCH-UI': ('ACD-15', 'G08', 'PRODUCT_SCOPE_SEPARATE', 'Research UI 另定；Model Review/Workspace 需求已明确，不能仍标无需求'),
}
urows = []
for r in read_csv(OLD / 'unresolved_items.csv'):
    ids, gates = related(r['path'])
    if r['item_id'] in external:
        ids, gates, action, needed = external[r['item_id']]
    elif r['evidence_status'] == 'STRUCTURAL_ROLE_KNOWN_RUNTIME_NOT_ASSERTED':
        action = 'REVIEW_TARGET_CONSUMERS'
        needed = '协议/重导出按目标消费者保全或移除；不要求虚构调用入口，不因 UNKNOWN 直接删'
    else:
        action = 'TARGETED_PROOF_BEFORE_CHANGE'
        needed = '只对拟改/拟删对象补具体构造/调用/注册/副作用；目标无必要后删除，不永久保留 UNKNOWN 实现'
    urows.append(dict(item_id=r['item_id'], path=r['path'], phase2_1_kind=r['kind'],
                      phase2_1_status=r['evidence_status'], decision_ids=ids, gate_ids=gates,
                      disposition=action, needed_evidence=needed,
                      old_production_evidence_required='false', source_fact_closed='false',
                      decision_gate_removed='true' if action == 'PREMISE_SUPERSEDED' else 'false'))
write_csv('unresolved_dispositions.csv', urows)
hotrows = []
for r in read_csv(OLD / 'architecture_truth_table.csv'):
    if r['granularity'] not in ('file','asset') or 'LARGE_HOTSPOT' not in r['fact_flags'].split(';'):
        continue
    path = r['path']
    ids, _ = related(path)
    action = 'REVIEW_ON_TARGET_CHANGE'
    note = '保留真实能力，按目标切口处理，LOC 不决定拆分或删除'
    special = {
        'src/agent/strategies.py': ('ACD-03;ACD-10', 'DELETE_NON_TARGET_AFTER_G05', '实验策略非产品目标；先救正式合同再删除'),
        'src/agent_execution/brain_action_loop.py': ('ACD-01;ACD-09', 'RETIRE_AFTER_QQ_CANONICAL', '双入口目标成立后删除旧 loop'),
        'src/session/store.py': ('ACD-04;ACD-09', 'RETIRE_QQ_AUTHORITY', '会话 PG 化及必要记忆重接后删除'),
        'src/orchestration/worker.py': ('ACD-02;ACD-09', 'COMPOSE_CANONICAL_AFTER_RETIREMENT', '先统一删旧，再整理构造与生命周期'),
        'src/orchestration/config.py': ('ACD-08', 'REMOVE_MIGRATION_CONFIG', '目标删除编排开关/非目标实验配置，校验真实能力'),
        'src/agent_activities/runtime.py': ('ACD-12;ACD-17', 'SPLIT_PREPARE_AND_INVOKE', '所有主模型 phase 统一冻结/调用边界'),
        'src/web_domain/services.py': ('ACD-04', 'PROMOTE_CONVERSATION_DOMAIN', '事务成为双 surface 核心，非 Web 专属'),
        'src/sandbox/tools/adapters/mcp.py': ('ACD-11;ACD-17', 'SPLIT_TRANSPORT_AND_FREEZE_SCHEMA', '以实际协议和快照合同验证'),
        'src/resources/model_client.py': ('ACD-17', 'FREEZE_EFFECTIVE_PAYLOAD', '最终转换/defaults 在批准前完成'),
        'src/web_api/app.py': ('ACD-04;ACD-15;ACD-18', 'KEEP_SURFACE_ADD_QUERY_BOUNDARIES', '领域移出，review/workspace 提供正式查询'),
        'docker-compose.yaml': ('ACD-08;ACD-13;ACD-16', 'ALIGN_CANONICAL_DEPLOYMENT', '删过渡 flag，保留真实服务与已实现拓扑'),
    }
    if path in special:
        ids, action, note = special[path]
    elif path.startswith(('test/', 'web/')) and ('test' in path or path.startswith('test/')):
        ids, action, note = 'ACD-09;ACD-12', 'RETAIN_BEHAVIOR_REPLACE_OLD_CONTRACTS', '必要回归迁到目标；仅旧合同断言可随实现删除'
    elif not path.startswith('src/'):
        ids, action, note = 'ACD-12;ACD-13;ACD-15', 'KEEP_PURPOSED_ASSET', '生产 UI/构建/验证/运维各按真实用途处理，不以 LOC 统一拆分'
    hotrows.append(dict(path=path, loc=r['loc'], reachability_class=r['reachability_class'],
                        decision_ids=ids, disposition=action, rationale=note,
                        implementation_status='NOT_IMPLEMENTED'))
write_csv('hotspot_dispositions.csv', hotrows)

write_text('04_fact_to_decision_review.md', '# 事实到决策的修订追踪 · R2.1\n\n' + BANNER +
    'R2 已修订产品前提与目标处置；R2.1 仅调整 ACD-01/04/17 的三个合同边界与引用，不改 2.1 代码分类。CURRENT 可达不代表 TARGET 应长期保留；'
    'UNKNOWN 不代表可直接删除。拟删对象补目标调用/注册证据后主动清理。\n\n'
    '## H01–H17\n\n| H | ACD | TARGET disposition |\n| --- | --- | --- |\n' +
    '\n'.join(f"| {r['hypothesis_id']} | {r['decision_ids']} | {r['disposition']} |" for r in hrows) +
    '\n\n## D01–D10\n\n| D | ACD | R2.1 处置 | 状态 |\n| --- | --- | --- | --- |\n' +
    '\n'.join(f"| {r['drift_id']} | {r['decision_ids']} | {r['disposition']} | {r['status']} |" for r in drows) +
    '\n\n## 83 项 U 的含义变化\n\n'
    'unresolved_dispositions.csv 保留每个原 ID/kind/status，source_fact_closed=false。'
    'U-DEPLOY/U-HISTORY/U-ACCOUNTS 标记 PREMISE_SUPERSEDED、decision_gate_removed=true：'
    '旧生产证据门禁被用户前提取消，绝不是源码/线上事实被证明。其余按目标局部补证、未来拓扑或独立产品范围处理。'
    '不要求旧历史数据、外部调用者永远兼容或 UNKNOWN 全清零。\n\n'
    'U-TOPOLOGY 仍是当前未实现事实，但加入 session-scoped workspace 演进候选；'
    'U-RESEARCH-UI 仍是 Research 专用 UI 范围，不能拿它否认已经明确的 Model Review/Workspace 需求。'
    '结构性协议根据目标消费者迁出保全；非目标实现根据 G05 删除。\n\n'
    '## 26 个大型文件/资产\n\n'
    'hotspot_dispositions.csv 按 2.1 truth table 的 file/asset 粒度覆盖全部 LARGE_HOTSPOT。'
    '实验 strategies、旧 BrainActionLoop、QQ SessionStore 改为目标验证后的退役；'
    'web_domain/services 提升中立领域；model runtime/client 增加准备/调用冻结切口。'
    '各测试保留必要业务回归、替换旧接线合同；benchmark/构建/UI 不因 LOC 自动整改。\n\n'
    '## 修订追溯\n\n'
    'change_type 保留 R2 相对 R1 的处置类别；R2.1 只修改 ACD-01/04/17，其他 15 个决策对象保持不变。'
    'R2 稿由 Git 提交 53007a2 保留；E44/E45 是本轮新增的定向证据。'
    '旧 R1 内容由 Git 提交 4c18f5d 保留，本目录只维护一套最新修订稿，不建立冲突的 old/current 两套目标。'
    'Machine registers 与摘要/目标/工作包均沿用同一组 ACD 与 R2.1 gate 编号。')

packages = [
 ('W0', '', 'ACD-01;ACD-04;ACD-06;ACD-14;ACD-17;ACD-18', 'G01', '冻结目标与文档职责'),
 ('W1', 'W0', 'ACD-01;ACD-04;ACD-08;ACD-17', 'G02;G03;G04', 'source-neutral 输入与共享 Run；suspend-safe Run/lease 分离、wait 释放资源、恢复新 fencing token；Web 与通用等待验证'),
 ('W2', 'W1', 'ACD-01;ACD-04;ACD-07', 'G03;G04;G06', 'QQ/Conversation 统一 PG admission authority；首版推荐 single active + busy reject，可替换 policy；双入口验证'),
 ('W3', 'W2', 'ACD-03;ACD-08;ACD-09;ACD-10', 'G03;G04;G05;G06;G11', '救出依赖后删除 legacy/非目标实现'),
 ('W4', 'W3', 'ACD-02;ACD-03', 'G02;G03', '唯一 runtime 装配与协议清理'),
 ('W5', 'W4', 'ACD-12;ACD-17', 'G03;G04;G12', '复用 W1 suspend/resume，接入 Prepare/Invoke/Snapshot/AuthorizationPolicy/Review；off/every_call 均逐快照授权，不重构 Run/lease'),
 ('W6', 'W4', 'ACD-16;ACD-18', 'G03;G13', 'Workspace Query 合同；worktree G07 独立后评'),
 ('W7', 'W5;W6', 'ACD-05;ACD-11;ACD-13', 'G09;G10;G11', 'File/MCP/build 能力结构整理'),
]
write_csv('work_packages.csv', [dict(work_package_id=i, depends_on=deps, decision_ids=ds,
    gate_ids=gs, target=target, implementation_status='NOT_IMPLEMENTED') for i,deps,ds,gs,target in packages])
candidates = [
 ('R01', 'src/orchestration/web_workflow.py', 'WebRunWorkflow', 'ACD-09', 'EXTRACT_THEN_DELETE', 'queue/FailureInput/其他共享 DTO 迁出，durable 生命周期就绪'),
 ('R02', 'src/orchestration/web_activities.py', 'execute_agent_activity', 'ACD-09', 'DELETE_SELECTED_SYMBOLS', '保留/中立化 prepare/lease/finalize；删除旧 Host 注入与执行 Activity'),
 ('R03', 'src/agent_execution/brain_action_loop.py', 'DefaultBrainActionLoop', 'ACD-01;ACD-09', 'DELETE_AFTER_TARGET', 'QQ/Web 两入口都使用 durable，必要语义已迁移'),
 ('R04', 'src/agent_execution/facade.py', 'AgentExecutionFacade', 'ACD-03;ACD-09', 'EXTRACT_THEN_DELETE', 'StableExecutionFailure/EventSink 等仍需合同迁出'),
 ('R05', 'src/agent_execution/qq_host.py', 'QQExecutionHost', 'ACD-01;ACD-09', 'REPLACE_THEN_DELETE', 'QQ PG 命令、记忆和 delivery 已接入'),
 ('R06', 'src/agent_execution/web_host.py', 'WebExecutionHost', 'ACD-01;ACD-09', 'REPLACE_THEN_DELETE', '中立 loader/lifecycle/event 等必要适配保全'),
 ('R07', 'src/orchestration/workflow.py', 'OrchestrationWorkflow', 'ACD-04;ACD-09', 'DELETE_SELECTED_SYMBOLS', 'ReflectWorkflow/MetricsReportWorkflow 必要能力单独保全'),
 ('R08', 'src/harness/activities.py', 'process_turn_activity', 'ACD-03;ACD-09', 'DELETE_SELECTED_SYMBOLS', 'archive/reflection/metrics/context/prompt 必要消费者迁出'),
 ('R09', 'src/session/store.py', 'SessionStore', 'ACD-04;ACD-09', 'REPLACE_THEN_DELETE', '正式会话权威转 PG，Action/Memory/Archive 全部必要消费者重接'),
 ('R10', 'src/agent', '', 'ACD-03;ACD-10', 'EXTRACT_THEN_DELETE', '正式 protocol 迁出；非目标实验/Multi-Agent 的所有入口、配置、测试处置'),
 ('R11', 'src/account/account_service.py', 'AccountService', 'ACD-10', 'DELETE_AFTER_TARGET', 'PG 身份/账户绑定目标测试与消费者核验'),
 ('R12', 'src/account/models.py', 'Account', 'ACD-10', 'DELETE_IF_NO_TARGET_CONSUMER', '检查 account/__init__ 与所有目标消费者，必要合同先迁出'),
 ('R13', 'scripts/merge-account.py', '', 'ACD-10', 'DELETE_OBSOLETE_OPS', '目标运维无 JSON 账号用途；不是执行脚本删除实际数据'),
 ('R14', 'src/orchestration/web_dispatcher.py', 'TemporalClientAdapter', 'ACD-08;ACD-09', 'REMOVE_BRANCH_KEEP_CAPABILITY', '删除 durable flag/legacy type 分支；保留目标 Outbox 幂等启动'),
 ('R15', 'config/agents.yaml', '', 'ACD-08;ACD-10', 'DELETE_NON_TARGET_CONFIG', '所有正式装配不再加载；不删除其他真实能力配置'),
 ('R16', 'persistence/migrations', '', 'ACD-10;ACD-13', 'KEEP_REQUIRED_SCHEMA_REVIEW_COMPAT_ONLY', '逐 SQL 对象审查 obsolete compatibility；全新 schema/函数/权限必须可创建'),
]
write_csv('retirement_candidates.csv', [dict(candidate_id=i, path=p, symbol=s,
    decision_ids=ds, disposition=action, prerequisites=pre, gate_ids='G03;G05;G11',
    earliest_package='W3', implementation_status='NOT_IMPLEMENTED',
    old_production_evidence_required='false') for i,p,s,ds,action,pre in candidates])

manifest.update(revision=REVISION, classification=register['classification'],
    review_status='TARGET_REVISED_NOT_IMPLEMENTED', premise_source='USER_ATTACHMENT',
    user_premise_id='U-PREPROD', user_premise=register['premise'],
    old_production_evidence_required=False, source_modifications=False,
    production_changes=False, production_tests_run=False, external_runtime_verified=False,
    decision_count=len(decisions), evidence_count=len(evidence),
    scope='Only phase2_2 audit/decision artifacts; target code, tests, docs and data unchanged.',
    counts=dict(decisions=len(decisions), evidence=len(evidence), hypotheses=len(hrows),
                drifts=len(drows), unresolved=len(urows), large_file_asset_hotspots=len(hotrows),
                backend_packages=30, work_packages=len(packages), retirement_candidates=len(candidates)),
    generation='decision_register.json is decision source; render_review.py projects registers and indices; narrative 00/02/03/06/07/08 is authored separately and cross-checked.',
    baseline_policy='Frozen review HEAD is provenance; later artifact-only commits do not invalidate evidence. Source hashes and outside-scope diff detect actual drift.')
manifest.pop('initial_tracked_diff', None)
manifest.pop('generated_at', None)
write_text('audit_manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
print(json.dumps(manifest['counts'], ensure_ascii=False))
