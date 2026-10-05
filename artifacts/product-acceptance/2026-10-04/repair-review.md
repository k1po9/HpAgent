# 修复检视与实施顺序

本次检视依据当前工作区代码、已有真实浏览器证据，以及补充的只读检查。后端 `/health/ready` 仍返回 ready。此次没有修改业务代码、配置、镜像、代理或数据库，没有再次调用模型；仅新增本修复检视。此前 F03 的 UUID 兼容修复与全部原有未提交修改保留。

建议先完成可独立验证的协议/UI 修复，再受控统一验收 Origin；并行于这些代码工作的环境诊断可以继续，但模型成果必须等真实模型恢复后验收。Document 镜像对齐需要独立部署检查。缺失入口仍是覆盖缺口，不作为此次自动补建事项。

## 逐项修复方案

| 项目 | 修改位置与最小方案 | 验证与通过条件 |
|---|---|---|
| F06 Work SSE 406 | [ProtocolMiddleware](../../../src/web_api/app.py#L198)：精确区分 Run `/events` 与 Work `/events/stream` 为 SSE；Work `/events` 为 JSON。保留其他接口的响应类型限制。 | 无数据库中间件回归覆盖三种路由 × 合法/错误 Accept；再用专用账号验证真实 Work SSE 为 200、Content-Type 为 text/event-stream，收到事件、断线恢复；Work JSON events 为 200。列表轮询成功不能替代 SSE 通过。 |
| F07 停止后授权加载错误 | [App](../../../web/src/App.tsx#L85)、[WorkspacePanel](../../../web/src/components/WorkspacePanel.tsx#L109)：资源候选使用独立的可选择 Run ID，只有 queued/running 才请求；切换/进入 cancelling 或终态时清空候选。保留版本操作所用 currentRunId。 | 测试 running→cancelling→cancelled；晚返回的候选响应不能恢复旧列表/错误；A→B→A 延迟返回不能串数据；已完成 Run 的已发布文件加载和版本操作仍可用。真实停止后候选区域为空并有解释，授权列表保持正常。 |
| F02 新建失败无提示 | [EmptySelection](../../../web/src/App.tsx#L196)：无选中对话时展示已有 workbench error，使用现有 clearError 与新建按钮，不另建错误状态。 | 新账号无对话，创建返回 403 或其他错误时显示安全提示；成功创建后提示消失；有对话时不重复显示 ChatPane 同一错误。 |
| F04 页面隐藏模型失败原因 | [RunStatus](../../../web/src/components/RunStatus.tsx#L60)：failed 时展示服务端已提供的安全 failure.message，必要时附 error code；保留 retryable 的既有重试限制。 | 用已有 model_unavailable 失败样本展示“模型暂时不可用”；成功/取消时不误报；不把原始异常、凭据或模型请求内容放入页面。此项只修提示，不能将模型执行标为通过。 |
| F08 Research 空白/文案 | [ResearchOutputs](../../../web/src/components/ResearchOutputs.tsx#L22)：区分首次未加载、加载中、成功为空、失败。空列表提示“暂无调查委托/成果”；现有字段加简短说明，例如“用于选择此次后台执行的编号”。 | 加载中不先显示“暂无”；成功空列表显示解释；失败保留错误而非伪空状态。只改善已有展示，不创建 Research 表单或记忆管理入口。 |
| F10 停止后仍显示未来到期 | [WorkPanel](../../../web/src/components/WorkPanel.tsx#L90)：stopped/completed 显示终态说明，隐藏仍暗示会继续执行的 continuation/due_at。paused 可以保留计划但明确已暂停。 | 已停止未来提醒不再显示“等待下一次到期”；active 的计划、paused 的提示与 completed 的完成凭据展示保持正常。无需改持久化状态或调度。 |
| F05 窄屏挤压 | [styles.css](../../../web/src/styles.css#L15)：增加窄屏布局断点，将现有侧栏与聊天改为合理堆叠并限制侧栏高度；控件换行、表单与长文本约束宽度。复用现有面板关闭动作。 | 实际浏览器检查 390×844、768px、1440px；scrollWidth 不大于视口；输入、发送、停止、对话导航和面板关闭可操作；Trace/Workspace/Artifact 不遮住无法关闭的操作。无需新增折叠导航功能。 |
| F03 UUID | [WorkspacePanel](../../../web/src/components/WorkspacePanel.tsx)：已有三处调用改用 newIdempotencyKey 的修复。 | 已通过真实 43-byte 文件上传、保存、下载一致性及相关 25 项测试。保留即可；不能由此推定 v2 成果链也通过。 |

F06 的窄范围识别示意：

```python
is_sse = bool(re.fullmatch(
    r"/api/v1/(?:runs/[^/]+/events|works/[^/]+/events/stream)",
    request.url.path,
))
```

这里只是修复示意，尚未写入业务源码。不要改成所有 `/events` 都是 SSE，也不要允许任意 Accept 来掩盖协议错误。可在现有 [中间件测试](../../../test/web_api/test_file_protocol_middleware.py#L1) 附近增加有行为意义的协商用例。

此次使用当前 ProtocolMiddleware 和临时内存 FastAPI 路由做了无数据库复现，结果如下：

| 路径 | Accept | 当前状态 |
|---|---|---|
| `/api/v1/runs/test/events` | `text/event-stream` | 200 |
| `/api/v1/works/test/events/stream` | `text/event-stream` | 406 |
| `/api/v1/works/test/events` | `application/json` | 406 |

临时路由仅复现响应协商，200 不代表真实流式事件已验证。已有浏览器的真实 Work SSE 406 证据见 [失败报告 F06](failures.md)。

F07 有两点必须保留：

1. `currentRunId` 同时用于已发布文件与版本提交；不能为修候选请求把所有终态 Run ID 全部置空。增加候选用途的 ID 或状态参数即可，候选分页也必须使用同一个 ID。
2. [ResourcePolicy.candidates](../../../src/workspace/resources.py#L230) 还要求 snapshot ready。404 本身无法证明是终态，也可能是不存在、账号不匹配或快照未就绪。前端仅对已确认发生切换/退出可选择状态的旧响应丢弃，保留未知失败和所有权检查；不能吞掉全部 404。所有授权刷新回写都需要核对当前对话/Run，防止过期响应覆盖新页面。

## 配置、环境与部署

### F01：统一 localhost 验收地址

既然验收约定为 `http://127.0.0.1:5173`，目标配置应为 `WEB_PUBLIC_ORIGIN=http://127.0.0.1:5173`。当前允许的 IP 只是此前继续验收的路径。`localhost` 和 `127.0.0.1` 也不是同一 Origin。

检查 Compose 使用的环境来源，修改这一项后，只更新 hpagent-api 服务。**普通 docker restart 不会加载更改后的环境变量**；需要 Compose 重新创建容器。下面是后续受控部署命令，此次未执行：

```bash
docker compose --profile web up -d --no-deps hpagent-api
```

如果此前还改了 Python ProtocolMiddleware，API 进程也必须重新启动才能运行新代码。重新创建服务后，检查实际 settings、ready 与 Fake=false；在 localhost 用专用账号重新登录、新建对话，再验证错误 Origin 和缺失 CSRF 仍为 403。不要扩大 CSRF 放行范围。原 IP 写操作此后受到同样的精确 Origin 限制，这属于统一地址的预期影响。

### F04：先确定模型失败原因，再改变调用配置

目前已证明真实聊天决策没有成功，尚未证明是代理、供应商负载、连接还是响应读取导致。查询改写成功也不能证明含工具 schema 的聊天请求正常。

建议按以下顺序排查，减少收费请求：

1. 在实际 hpagent Worker 容器中只读核对 endpoint、api_format、有效 timeout、max_tokens、extra_body 与代理是否存在；核对 DNS/TLS/连接路径。不得输出密钥或把完整请求体写入验收证据。
2. 优先从既有异常链或安全日志区分 ConnectTimeout、ReadTimeout、HTTP 状态与解析错误。当前 [ModelClient](../../../src/resources/model_client.py#L246) 保留底层异常类到 ModelDispatchError.reason，但 [ResourcePool](../../../src/resources/resource_pool.py#L383) 的常规失败日志只打印外层类名。若现有证据无法确定，可以最小补充枚举化的异常类别、状态码、端点 ID 和耗时日志，不记录异常全文、响应体、Header 或 prompt。
3. 有原因证据后才决定修地址/网络、请求兼容参数或读取超时。有效客户端来自模型链 entry 的 endpoint.extra；不能仅修改 channel_overrides.web 就假定 Worker 的客户端超时已经变化。改动后核对启动日志中的实际值。
4. 路径恢复后只发一个短聊天输入，确认 Run 终态成功、页面助手消息与持久化消息一致、刷新恢复、实际 model dispatch 成功。随后分别验 Work/Research/Artifact/文档成果；每个场景保持小输入。

不先增加重试次数、不盲目延长全部 timeout、不调用 proxy_all_on。结果 uncertain 的请求可能已经到达供应商，反复重试不能作为诊断方法。

Embedding 超时与 Hindsight recall 3 秒降级是另外的链路。Hindsight healthy 且 skip LLM verification=true，仍不能推断长期记忆正常。模型恢复后必须确认测试聊天完成、retain/outbox 成功，再开另一对话核对 recall 命中与账号隔离；空列表或无错误不算通过。

### F09：受控部署当前 Document Worker

Compose 的文档 Worker 使用镜像内源码，没有源码 bind mount。只 restart 同一个旧镜像不会消除 Activity 漂移。应从当前保留未提交修复的工作区构建并部署该服务，同时核对预算、Tracing、ResourcePolicy 相关依赖。当前 [Dockerfile.document](../../../src/Dockerfile.document#L1) 通过 COPY 将 src 打入镜像，依赖由 requirements-document.txt 安装。

后续命令示意，此次未执行：

```bash
docker compose --profile web build hpagent-document-worker
docker compose --profile web up -d --no-deps hpagent-document-worker
```

构建之前记录原镜像 ID、容器配置和源码 hash，确认没有在途文档任务；构建完成核对实际镜像与模块 hash，再检查 schema verification、Temporal 注册与退出错误。保留原命名卷，不做 down -v、数据库重置或 Docker daemon 重启。镜像依赖安装可能需要网络，应沿用现有代理配置诊断，不切换全局代理。

`WEB_FILE_TRANSFORM_ENABLED=false` 是独立限制。先核对当前产品契约下验收的文档类型与依赖是否就绪，确认测试需要且已有实现支持时才受控调整能力开关；对齐镜像不能替代开启条件检查，开启开关也不能替代真实文档执行。

文档通过条件至少包括：小文件的真实执行终态、最终文件可下载且内容正确、页面与持久化成果一致、撤权边界及 B 账号不可访问。旧镜像缺少当前重新鉴权代码是部署偏差，当前未复现越权攻击，不能把它写成已证明的数据泄露。

## 实施顺序与回归范围

1. 先修 F06、F07、F02 和失败消息展示。协议回归、候选生命周期/延迟响应与现有 App/RunStatus 测试是这一批重点。保留已有 workbench 对快速切换的修复。
2. 补 F08/F10 文案状态与 F05 窄屏布局。可逆文案修改用浏览器检查即可；不为每条文案机械新增测试。布局必须实际查看截图和操作按钮。
3. 合并服务更新时统一 localhost Origin，受控更新 API，复验 CSRF、账号隔离、Work SSE。前端静态测试通过不能替代部署后浏览器签收。
4. 模型与记忆环境逐项诊断；Document Worker 单独构建、对齐并验边界。按 [人工教程](manual.md) 继续尚未验证的真实成果场景。
5. 更新覆盖矩阵时逐项附页面、网络和必要后端证据。API 接受、列表展示、轮询变化均不能代替执行成果。缺少 Web 入口与 QQ 测试身份的项目保持原分类。

Python 中间件回归不依赖数据库；涉及资源所有权/文档持久化的集成测试应使用隔离测试环境，不能把 pytest 数据夹具指向现有业务库。真实浏览器验收沿用专用账号和小数据，QQ 完成绑定仍需获准测试 QQ，不自动联系真实用户。

当前仍不具备完整真实产品浏览器 E2E 通过条件。此次检视没有关闭任何原失败；F03 保持此前已修复状态。历史 17/17 Fake Executor E2E 与 36 项非 PostgreSQL 单元失败保持其原有结论，不能宣称全量 CI 已通过。
