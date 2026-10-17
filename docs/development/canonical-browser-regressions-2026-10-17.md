# Canonical 浏览器回归修复（2026-10-17）

本轮承接未提交的清理/文档/脚本收束工作。没有 reset、恢复退役文件、修改迁移、提交、推送或部署；没有连接业务库、使用现有 File Store、调用真实供应商或停止现有服务。起始状态和差异、逐轮 trace/报告、运行配置和日志保存在 `web/test-results/canonical-fix-20261017/`。原 `web/test-results/canonical/` 保留。

## 三项失败的边界

### 成果刷新：产品发现生命周期缺口

原 trace 与本轮开发服务器复现均在刷新后读取会话/消息成功，却没有消息成果 GET，页面只剩禁用的生成按钮。本轮记录显示刷新后 `scrollTop=0`、`scrollHeight=1026`，成果 footer 位于 y=973；IntersectionObserver 给出 `isIntersecting=false`，此时祖先仍 inert。祖先释放 inert 后不再产生交叉事件，阅读初始化也只监听消息 DOM，遗漏了祖先状态释放。因此不能用一次 footer 可见性事件承担成果发现。

`ArtifactMessageItems` 改为观察整条消息，主动检查几何位置，在阅读/外壳的 hidden、inert、retiring 变化及滚动时重检；最新一条合格助手消息在可交互时额外进行一次有界发现。每次挂载最多派发一次自动发现，未进入可见范围的旧消息不因挂载而全部查询。`HpThread` 同时在祖先 hidden/inert 释放时重试已有阅读对齐逻辑。存储层未改，继续使用原请求去重、最多四并发、显式错误重试、账户 generation 和对象 epoch 校验。

浏览器测试记录真实 POST 得到的 artifact_id/version_id；刷新 GET 的 summary 必须返回同一对 ID，打开同一卡片后 srcdoc 与原预览完全一致。扩展覆盖长回答末尾、窄屏、减动、挂起成果 GET，以及 Alice 挂起结果在 Bob 登录后到达时的隔离和服务端拒绝。组件测试另模拟 observer 完全不回调及祖先 inert 释放，验证无需单次交叉事件也能发现；验证屏外旧消息不会全部加载。

### 历史前插：测试准备竞态

`history-baseline-events.json` 保留实际程序写入栈和 DOM/scroll 事件。复现顺序：测试在 1243ms 写入 900，此时 inert=true；1252ms 的 scroll 同样发生在 inert=true，应用按接受契约忽略它。2166ms 过渡结束，`HpThread.align` 仍读到默认 atBottom=true，写入 17877，实际贴底 17430；随后布局变为 17390。分页提交后同一函数写入 21442，实际贴底 20955。最终跳变来自初始化/分页沿用默认贴底状态，不是已记录离底阅读状态后的锚点补偿被覆盖。

测试现在等待阅读区解除 inert/hidden 且初始化贴底完成，再写入 900，并等待“有新消息 / 回到底部”出现，确认应用已记录离底状态。保留原 `toBeCloseTo(..., 0)` 锚点及流式输出位置断言。扩展同一用例验证第二次慢分页、请求挂起期间用户继续滚动、实时追加及流式完成不抢位置、点击回底后恢复跟随，以及第三次分页在切换会话后返回不污染新会话。没有增加固定 sleep、位置容差、skip/fixme，未改 workbench 分页或 conversationUi 状态契约。

### UI8 启动：资源中断，未证明认证缺陷

原 trace 多个 TSX/依赖模块出现 `net::ERR_NETWORK_CHANGED`，在登录标题渲染前已经中断。本轮开发环境也在成果用例整页重载时捕获同类多个模块失败；该失败及 trace 保留在 `focused-expanded/`，不会被生产预览的成功覆盖。没有证据支持修改认证、instrument 或应用启动逻辑。

只读网络监控观察到现有 `hpagent-hindsight-1` 约每30秒重启，同时在原 `hpagent_app-network` 上增删 veth/IPv6 地址和路由。它是网络变化的具体环境线索；监控开始晚于部分失败，不能证明它解释所有历史错误，也不宣称已修复宿主网络。本轮未调整代理、VPN、网卡或该容器。

Canonical 默认改用独立生产构建和 Vite 静态预览，`e2e-preview.mjs` 每次创建临时构建目录，转发停止信号并移除自己的目录，输出构建/预览退出状态；不覆盖 web/dist，也不复用评审服务。通用 playwright.config.ts 继续提供开发服务器入口。helpers 在登录导航前安装 requestfailed、pageerror、console error 和 crash 记录，每个测试附带 browser-diagnostics；可选阅读诊断记录滚动写入调用栈、DOM 变化、消息首尾 ID、相对锚点和界面贴底状态。服务日志与退出信息由 Playwright webServer 输出进入独立运行日志。

### 完整确认中发现的独立环境问题：复用 Fake 环境耗尽后台容量

第一轮完整29/29通过后，最初第二轮复用了前面多轮聚焦/完整测试的同一套 Fake API/数据库，结果28/29。UI8此次登录和聊天成功；成果POST 202、后续版本GET 200，但版本一直queued，没有对应构建Run。隔离数据库读证据确认其Work continuation为 `ready / waiting_capacity`，账户累积的四个后台Run（两个提醒、两个研究）仍queued，协调准入容量已占满。`CapacityService.admission` 的 WORK_ACCOUNT_COORDINATORS 默认值为4；Fake Executor只完成chat和artifact，不完成提醒/研究，跨运行复用使这些演示任务累积。这与原来的启动ERR_NETWORK_CHANGED是不同失败。

保留 `full-2/`、`full-2-report/`、API日志及 `full-2-capacity-evidence.txt`。没有放宽容量保护、增加超时、跳过UI8或改Fake为成功；后续两个完整确认各自使用新PG/Redis/API/File Store，运行后仅回收自己的环境。全新环境之间不清理或触碰业务数据，也不承诺重复复用同一Fake数据库支持所有Work执行器。

## 隔离与验证

专属 Docker 网络、tmpfs PostgreSQL 16 和 Redis 7，Fake API 使用离线镜像及只读源码/迁移挂载，File Store 位于新 API 容器内 `/tmp/test-files`。迁移前显式核实三个 DSN 都指向新网络的 `test-pg/hpagent_test`，API/worker/migrate 使用各自角色；Redis 为新 `test-redis` 的15号库。API 与前端分别使用 127.0.0.1:53303 和 localhost:54869（监听127.0.0.1），页面 baseURL、Cookie 和 WEB_PUBLIC_ORIGIN 一致为 http://localhost:54869，浏览器通过同源代理访问 API，没有连接现有业务端口。

原29项没有拆分或删除，保留相同 testMatch 和测试标题；仅扩展其中两项。生产聚焦3/3通过。全量单元70文件605项通过（原603项加两项组件回归），lint、typecheck、独立目录生产构建通过；既有大于500kB chunk警告保留。最初完整第一轮在 retries=0 时29/29通过（3.2分钟）；复用环境第二轮为28/29，原因与后续两次全新环境确认及回收记录见下方。

## 失败保全与限制

`baseline/` 保留成果/历史修改前的聚焦复现失败，以及同轮UI8通过记录。`focused-dev/` 保留编写增强断言时把 srcdoc 读取放在 Inspector 关闭之后产生的测试错误，已将读取移到关闭前。`focused-expanded/` 保留后续开发模块 ERR_NETWORK_CHANGED；该轮历史与UI8通过。`focused-production/` 为最终三项聚焦成功报告。首次组件扩展失败来自 Zustand 状态复制后 spy 调用记录残留；测试显式清理和 mockReset 后7/7、全量605/605通过。各次失败日志保留，未仅留下重跑成功。

验证范围是 Linux Chromium、Fake Executor 与真实隔离 API/PG/Redis，不替代真实模型、生产 Worker、QQ 外发、其他浏览器或真机验收。生产资源减少开发模块加载数量，不保证宿主持续网络变化下绝无中断。

## 完成记录

最终确认使用 Node 22.23.3、Playwright 1.62.1 / Linux Chromium，两个独立空库运行均关闭重试：

| 运行 | API / 前端端口 | 结果 | 报告 |
| --- | --- | --- | --- |
| confirm-1 | 39443 / 55097 | 29/29，0 skipped、0 flaky，193.3秒 | `web/test-results/canonical-fix-20261017/confirm-1-report/index.html` |
| confirm-2 | 41781 / 47507 | 29/29，0 skipped、0 flaky | `web/test-results/canonical-fix-20261017/confirm-2-report/index.html` |

JSON报告中的项目retries均为0；每轮配置、环境和回收清单与报告同目录。两轮包括完整UI8业务及资源释放断言，均非仅登录通过。最新三项聚焦覆盖也在这两轮中各通过一次。`canonical-29-mapping.json` 逐项核对原29项名称/文件，没有拆分、更名或删除。最早完整运行另有29/29，复用环境失败的28/29报告独立保留。

相对起始工作区，修改8个已有文件，新增下面最后两项：

| 文件 | 关键位置/用途 |
| --- | --- |
| `web/src/components/artifact/ArtifactMessageItems.tsx:19` | 有界主动发现、消息可见性及祖先状态重检 |
| `web/src/adapters/assistant-ui/HpThread.tsx:151`、`:403` | 最新合格消息标记；祖先释放后阅读对齐 |
| `web/src/components/artifact/ArtifactMessageItems.test.tsx:99` | observer无回调、inert释放及屏外历史回归 |
| `web/e2e/artifact.spec.ts:12` | ID/版本及同源内容恢复、长回答、慢查询、账户切换 |
| `web/e2e/ui-2-ai.spec.ts:320` | 同步就绪与阅读状态；连续/慢分页、流式、回底及会话切换 |
| `web/e2e/helpers.ts:73` | 启动错误附件与可选阅读事件诊断 |
| `web/e2e/ui-3-execution.spec.ts:5` | 独立证据目录参数，业务断言不变 |
| `web/playwright.r416.config.ts:19` | Canonical生产预览、退出日志及正常停止 |
| `web/scripts/e2e-preview.mjs`（新增） | 唯一临时构建、严格端口、停止转发与目录回收 |
| 本报告（新增） | 根因、边界、失败保全及验证记录 |

先前1762个其他diff条目逐字不变，原删除全部保持删除。存储层、迁移历史、认证及动效参数未改。`git diff --check`通过；lint、应用typecheck、受影响E2E/配置的严格TypeScript检查、独立目录生产build和70文件605项单测通过。前端临时配置已移入证据目录，未留在源码入口。

专属容器/网络和临时生产目录已回收，既有服务不停止。第一版预览默认被Playwright强制杀掉时遗留的本轮临时目录亦按本轮构建日志逐个清理；最终配置使用SIGTERM并在两次完整确认中验证自动回收。初版隔离确认包装器出现过配置模板替换语法错误（未启动浏览器），对应`fresh-1.log`、`fresh-2.log`保留；修正后使用不同的`confirm-*`目录，未覆盖错误记录。
