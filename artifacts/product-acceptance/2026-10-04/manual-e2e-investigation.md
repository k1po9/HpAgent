# 人工 E2E 四项反馈：调查与实施方案

调查日期：2026-10-04，时间均为 Asia/Shanghai。范围是当前工作目录、实际运行的 Worker、现有 PostgreSQL 记录及结构化日志。本轮只新增本报告及[脱敏证据摘要](./manual-e2e-investigation-evidence.json)，未修改业务代码、数据库或部署，也未发起新的真实模型请求。调查开始时仓库已有未提交修复，本报告以包含这些修复的当前代码为准。

## 1. 选择“资料”后点击新建目录返回 409

**判断：前端把新建与改名/移动共用一套字段，导致新建请求默认指向错误的位置并重复使用已有名称。**

- 日志准确命中 `req_01a10575-ab84-7310-9175-7460282fd454`，时间为 13:49:20.173，错误为 `workspace_conflict`。
- [WorkspacePanel.tsx:216](/home/hp/workspace/HpAgent_web/web/src/components/WorkspacePanel.tsx:216) 的 `selectNode` 将 `name` 填成所选节点的名称，将 `parentId` 填成所选节点的父目录。
- [WorkspacePanel.tsx:608](/home/hp/workspace/HpAgent_web/web/src/components/WorkspacePanel.tsx:608) 的“新建目录”直接调用 `createWorkspaceDirectory(parentId, name)`。
- 因此选中“资料”后直接点击新建，实际意图成为“在根目录再建一个资料”，不是“在资料下面新建子目录”。如果只改名称，也会把目录建在根目录，除非再手动修改目标目录。
- 同级名称唯一约束正确拒绝重复入口；后端没有发生目录存储失效。但 [app.py:965](/home/hp/workspace/HpAgent_web/src/web_api/app.py:965) 把多种数据库约束异常都转成相同的 409，返回的 `details` 和日志都没有保留可用的冲突原因。

历史请求正文没有保存，无法还原当时是否手动改过字段；以上是当前代码对用户描述操作的确定性解释。

**修改方案：**

1. 拆成“在此目录中新建”与“编辑所选节点”两套表单状态。新建使用独立的 `newDirectoryName`；默认父目录为所选目录，选中文件时为该文件所在目录。新建名称初始为空。
2. 新建表单显示完整目标路径；改名/移动继续使用当前节点名称和其父目录。新建成功后选中新目录，刷新时保留有效选择，避免 `refreshSignal` 将目标重置到根目录。
3. 后端按已知业务原因区分 `name_exists`、`invalid_name`、`invalid_parent`、`directory_not_empty` 等原因，保留现有通用错误码兼容性；UI 展示可操作的错误及 request ID。未识别的数据库异常按内部错误记录，不能一概伪装成名称冲突。

**验收：**选中资料 → 输入一个新名称 → 新目录的 `parent_id` 必须是资料；同级重复名称仍返回冲突并显示原因；不同父目录可用同名目录；刷新与上传完成后不丢失目标选择；改名/移动仍操作原节点。

## 2. 长期 Workspace 上传文件后，下一轮模型说看不到

**判断：已确认有三个问题叠加：上传没有授予对话使用权、上下文缺少长期文件候选提示、环境提示仍沿用 Git 工作区概念。没有证据表明这是上传尚未完成或缓存刷新延迟。**

实际数据：

- root 账户 ID：`01a1056d-02cc-7b8b-89d4-aef33f0d3b01`。
- 两个文件均为 `ready`，分别于 13:46:38 和 13:56:07 完成上传，并已进入长期目录；第一个文件早于 13:54:44 的“能看到吗”提问。
- 两个文件的来源均为账户 Workspace，不属于某个 Conversation。
- 该账户 `resource_grants` 为 **0 条**；相关六个 Run 的长期候选数均为 **0**；`run_files` 为 **0 条**。
- 后续三个聊天 Run 实际调用了 `list_run_candidates`，均成功返回 `{"count":0,"next":null,"candidates":[]}`。所以不仅是模型没主动查，它查到的范围也确实为空。

代码对应：

- [WorkspacePanel.tsx:290](/home/hp/workspace/HpAgent_web/web/src/components/WorkspacePanel.tsx:290) 上传仅执行创建上传、写入内容、保存长期入口，不执行授权。“授权当前对话读取”是面板下方的独立操作。
- [resources.py:164](/home/hp/workspace/HpAgent_web/src/workspace/resources.py:164) 在 Run 创建时从当前主体的 `list_metadata` 授权冻结候选，没有授权就没有候选。这符合[现有权限契约](/home/hp/workspace/HpAgent_web/docs/architecture/workspace-v4.1.md:11)：账户所有者能浏览目录，不等于每个对话中的 Agent 都能读取。
- [context_assembly.py:99](/home/hp/workspace/HpAgent_web/src/application/context_assembly.py:99) 只加载已绑定 Run 的文件；[同文件:214](/home/hp/workspace/HpAgent_web/src/application/context_assembly.py:214) 只把这些文件格式化进上下文，没有长期候选清单。没有 Run 文件时连明确的空清单也不输出。
- [environment.yaml:8](/home/hp/workspace/HpAgent_web/config/prompts/environment.yaml:8) 将 Persistent Workspace 解释为 Account 级 Git 工作区，实际长期文件已经是虚拟文件目录。13:54:53 的真实模型快照仍有 Git branch 提示，没有 `## Current Run Files` 资源清单，也没有 `list_run_candidates` 使用指引；候选工具本身已经在请求中，因此无需把它再改成常驻工具。

**修改方案：**

1. 上传入口提供明确动作“上传并供当前对话使用”和“仅保存到长期 Workspace”。前者保存后对新文件授予 `list_metadata`、`read_content`；显示正在授权的对话名称。无当前对话时只保存，并给出选择对话的入口。不要默认给所有对话授权。
2. 保存与授权是两个阶段：如果保存成功但授权失败，显示“文件已保存，授权未完成”，允许补授权，不要求重复上传。补授权前查询当前规则，避免现有 `grant` 插入重复规则。完成后分别刷新目录和当前对话资源。
3. 每次新 Run 的初始上下文包含明确的 Run 文件清单，以及当前授权范围内的长期候选数量、第一页元数据和分页指引；元数据经当前权限检查，不注入其他对话未授权的文件名或正文。
4. 说明读取流程：`list_run_candidates` → `select_run_candidate` → 按返回的逻辑名读取。清单为空时说明“当前对话尚无可用资料，可在 Workspace 授权”，避免直接要求重复上传。
5. 环境提示区分长期文件目录、当前 Run 文件与可选的 Git 代码工作区。普通上传文件不依赖 Git。
6. 保持当前 Run 候选冻结契约：运行中新增上传/授权在**下一次执行**可用，界面明确提示；不能通过实时刷新扩大已运行任务的权限范围。

**验收：**上传并授权后下一轮直接发现并读取文件；对资料目录已有递归授权时新增文件也在下一轮出现；仅保存时明确提示授权方式；切换未授权对话不可见；运行中上传不改变已冻结候选；授权撤销后禁止读取与后续分发；授权失败时文件不被重复上传。

## 3. root 的五次聊天测试，后段经常失败

**判断：五次聊天里有三次失败。三次均为工具执行成功后，第二轮模型请求携带错误的工具调用历史格式，HTTP 500 被统一包装成 `model_unavailable`。已证实序列化缺陷，且与三次失败的位置一致。**

| 创建时间 | Run ID | 结果 | 实际失败阶段 |
|---|---|---|---|
| 13:51:19 | `01a10577-7f4b-74b6-8bc3-404ed640a3cf` | succeeded | 无工具往返，首轮模型返回完成 |
| 13:54:44 | `01a1057a-9e8c-7e22-a712-5547ba44f58e` | succeeded | 模型直接回答看不到文件 |
| 13:56:52 | `01a1057c-9450-7fb1-892d-c36aaab319c0` | failed | 两个工具成功，第二轮模型连续三次 HTTP 500 |
| 14:31:48 | `01a1059c-8dc7-7840-8926-d8d7ce3264ea` | failed | 候选查询成功，第二轮模型连续三次 HTTP 500 |
| 14:39:53 | `01a105a3-f56c-7b0f-b1b3-0bc5a8105b69` | failed | 候选查询成功，第二轮模型连续三次 HTTP 500 |

根因路径：

1. [runtime.py:550](/home/hp/workspace/HpAgent_web/src/agent_activities/runtime.py:550) 持久化内部工具调用格式：

   ```json
   {"id":"call_x","name":"list_run_candidates","arguments":{"limit":50}}
   ```

2. [model_client.py:331](/home/hp/workspace/HpAgent_web/src/resources/model_client.py:331) 只转换 Anthropic 的 `content[]/tool_use` 消息，遇到这种字符串内容加顶层 `tool_calls` 的内部消息，直接原样拷贝。
3. 实际 `provider_request_body` 已证明三个失败 Run 的第二轮请求均携带上述内部格式，重试也相同；共发现 **12 个**不合格的工具调用记录。OpenAI 格式应为：

   ```json
   {"id":"call_x","type":"function","function":{"name":"list_run_candidates","arguments":"{\"limit\":50}"}}
   ```

4. 在当前运行 Worker 内离线执行 `ModelClient.prepare_request`，确认仍输出内部格式，同时缺少 `type=function`、嵌套 `function` 与 JSON 字符串参数。这不是仅从源码猜测。
5. 提供方对第二轮请求约 1 秒返回 HTTP 500；Activity 整体约 2 秒失败。客户端只记录 HTTP 状态，[runtime.py:624](/home/hp/workspace/HpAgent_web/src/agent_activities/runtime.py:624) 再统一转成“模型暂时不可用”。所以 UI 丢失了具体原因，三次重试也无法修正确定性的错误格式。

原始提供方错误响应正文未保存，因此尚不能断言其服务器内部报错的全部细节。确定的是请求不合格、失败发生于工具往返、三次同样重试均失败；应优先修复此项，再做一次真实复验。已查记录不支持把这三次失败归因于本地断网、工具执行失败或账户权限不足。

**修改方案：**

1. 在模型请求边界统一序列化内部 transcript；OpenAI 输出正确的 `tool_calls/function/arguments`，Anthropic 输出 `tool_use/tool_result`。内部持久化结构无需到处改成某一家接口格式。
2. 发起 HTTP 前校验工具调用 ID、参数格式、结果配对、多工具结果的完整性。正常用户消息不能靠“还有待匹配工具 ID”被误认成工具结果。格式错误以独立、不可重试的错误结束，并在额度预留和 dispatch 前拒绝。
3. 增加脱敏的失败分类：请求格式、HTTP 错误、读取超时、连接失败、响应解析、预算与执行租约。保留 HTTP 状态、提供方错误代码/request ID、Run ID、快照 ID、模型阶段与重试次数；不把原始请求正文或凭据写进普通日志。
4. 聊天端可复用 Artifact 已有的异常链分类思路，保留根异常，不再把所有异常都展示成相同的 `model_unavailable`。本地格式错误不重试；明确的临时 HTTP 错误按既有有限次数策略重试。
5. 更新 serializer 版本；保留旧快照与历史记账。修复后的恢复通过新的安全执行/重试身份产生新快照，不能覆盖旧请求，也不能在同一幂等 operation 下偷偷改变请求内容。

**验收：**单工具、同轮多工具、连续两轮工具均能生成符合协议的真实发送体；错误/孤立工具结果在 HTTP 前被拒绝；正常用户消息不被转换成工具结果；模型输入快照与发送体一致；修复后对同样的文件查询做一次真实工具往返，确认第二轮完成。

**另一个独立问题：成果生成时间预算。**

13:52:58 创建的 Artifact Run `01a10579-0124-7749-a5da-d654b9bde5c8` 最终 succeeded、成果 completed，总耗时 128.4 秒；前三次生成各在约 30.1 秒触发 `ReadTimeout`，第四次于 13:55:06 成功。它不属于上表五次聊天失败，但会造成“接近完成时又重试”的体验。

[config/models.yaml:72](/home/hp/workspace/HpAgent_web/config/models.yaml:72) 的 chat endpoint 读取超时是 30 秒；[generator.py:101](/home/hp/workspace/HpAgent_web/src/web_artifacts/generator.py:101) 的成果生成共用 chat 且非流式。建议独立设置成果生成阶段的读取预算，先用可配置的 90 秒验证，并匹配外层 Activity/Run 预算；连接超时仍保持短值。当前只能确认读取等待触发超时，不能凭这些日志确定是提供方排队、生成慢还是传输停顿。不应仅调大超时来掩盖前面的工具格式缺陷。

14:45:35 的数据库连接拒绝发生在上述聊天结束之后，随后服务重启并健康；不能用它解释 13:57、14:32、14:40 的失败。

## 4. UI 混杂，影响后端功能人工验证

**判断：多个业务功能同时占用聊天布局，且部分功能只提供结果/控制入口，创建入口依赖模型。仅调整间距无法满足本次验证需求。**

[App.tsx:175](/home/hp/workspace/HpAgent_web/web/src/App.tsx:175) 将 Work、Workspace、Research 和 Chat 依次放在同一列，Trace/Artifact 再作为侧栏加入。[WorkspacePanel.tsx:274](/home/hp/workspace/HpAgent_web/web/src/components/WorkspacePanel.tsx:274) 把上传、搜索、目录树、编辑、版本、保留说明、授权等功能放在一个最大 350px 的滚动面板里；工作和研究还有各自高度上限。授权与高级操作容易藏在多层滚动中。

此外还有一个确定的交互缺陷：[ChatPane.tsx:48](/home/hp/workspace/HpAgent_web/web/src/components/ChatPane.tsx:48) 判断策略选择器是否锁定时仍使用旧终态 `completed`，而当前 Run 的成功状态是 `succeeded`。因此成功完成后策略选择器继续被锁住，失败后反而可以切换。这只能解释策略切换受阻，不能据此推断所有按钮都不可操作。

**最小方案：采用普通导航与独立内容页面，复用现有 API 和组件，不做视觉重设计。**

| 页面 | 本轮后续实施应提供的操作 |
|---|---|
| 对话 | 消息、策略、附件、取消/重试；展示当前对话可用资料；其他业务面板移出聊天区域 |
| 长期文件 | 全高目录树、上传/下载/搜索；独立的新建与编辑表单；版本和保留说明作为所选文件详情 |
| 资料授权 | 当前对话/工作选择、授权规则、附件可用性、撤销；明确显示下一轮生效与活动 Run 停止状态 |
| 持续工作 | 列表、创建、修订、暂停/恢复/停止、额度、输入文件、结果确认；详情显示 Work 和 Run 状态 |
| 研究 | 创建研究、执行历史、报告/输出、长期保存；工作尚未完成时显示具体阶段 |
| 成果 | 从已有消息或工作创建、版本修改、预览/下载；保存时明确选择长期目录 |
| 执行诊断 | 选择 Run，查看阶段、Trace、模型输入快照与失败分类；遵循现有 prompt_visibility 权限 |
| 账户 | 登录身份、QQ 绑定及退出 |

实施边界：

- 导航可用轻量 hash/state 完成，不必新增路由库。全局保留当前对话与所选 Run；各页面有完整滚动区和可见操作按钮。
- 先复用现有组件和数据状态，Work/Research 面板改成页面模式，不要求先展开 details。
- 工作/研究页增加最小创建表单，经现有 `POST /api/v1/works` 提交合法 Requirement；前端当前缺少对应创建方法，需要补上。提醒、研究、通用工作的字段和默认值由现有 Requirement 契约决定。表单创建不依赖 LLM，但研究与通用工作的后续执行仍可能需要模型。
- 保存目的地状态独立于 WorkspacePanel 是否挂载；消息与研究成果的保存按钮提供目的地选择，避免切页后隐藏依赖失效。
- 聊天草稿、附件、订阅及当前 Run 跟踪由共享状态维护；切页面不取消后台任务，回到对话恢复最新快照。页面与账户切换仍保留已有异步响应隔离措施。
- 策略选择器复用 workbench 已有的 `isTerminalRunStatus`，成功完成后解锁；Run、Work、Trace、Artifact 各自的状态枚举分别使用，避免再把 `completed` 与 `succeeded` 混用。
- Request ID、Run/Work ID、输入快照等放在诊断详情；普通操作显示明确成功/失败及当前对象，不需要用户手填 UUID 才能完成基本测试。

**验收：**常用桌面尺寸及窄屏都能完成新建目录、上传、授权、创建工作/研究、取消、查看报告、提交版本和保存成果；切页后返回状态一致；无当前对话时仍能管理账户长期文件；每项操作显示明确反馈；工作与研究创建可直接验证 API 契约；Run 成功后能够切换策略，活动 Run 期间仍锁定。

## 实施顺序与本轮验证边界

1. **最高优先：问题 3 的工具历史序列化与发起前校验。**不先解决它，模型查文件和许多后端功能的工具往返都会被阻断。
2. **随后：问题 1 的独立新建表单，与问题 4 的基础页面导航。**复用现有逻辑，先让人工测试操作可达。
3. **随后：问题 2 的显式上传授权、提示词和有界资源上下文。**上传与主体授权衔接完成后，再验证跨轮读取与权限边界。
4. **完成测试入口：Work/Research 创建表单、独立诊断页与阶段错误反馈；单独验证 Artifact 读取超时预算。**

本轮已执行：Python 的模型输入、分发日志、上下文组装相关测试 **18/18 通过**；前端 App/WorkspacePanel 相关测试 **12/12 通过**；运行 Worker 内的离线请求序列化复现确认存在缺陷。现有绿色测试未覆盖“选中目录后直接新建”“账户上传后下一轮发现”和“内部工具 transcript 到实际 Provider 格式的往返”。

本轮未执行修复后的真实模型调用或新增浏览器验收；方案尚未实施，不能把现有测试通过当作四项问题已修复。详细运行时间与计数保存在证据摘要中。
