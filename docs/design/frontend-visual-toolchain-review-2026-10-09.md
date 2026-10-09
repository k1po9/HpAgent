# HpAgent 前端视觉增强工具接入与功能边界审查

日期：2026-10-09（Asia/Shanghai）。审查分支：`main`；提交：`7e77631d9c092bf864655099f15f756c69021d67`。未切换分支。前一轮视觉指导、原图与文档索引修改保留。

**结论：建议采用 Impeccable 受限视觉评审 + React Grab 开发态源码定位 + 隔离的 Playwright CLI + 原有 Playwright Test。先完成最小工具试点，不进行全量 UI 重构。Motion、VibeCurb 和 Playwright MCP 暂不进入默认工具链。**

本轮完成源码、依赖、上游文档及包元数据核对，并用已安装的 Playwright 执行一次无后端 Chromium 启动检查。没有安装第三方工具，没有改动应用源码、依赖锁、API 或测试配置，没有运行全仓库 CI、大规模 E2E 或后端服务。下文“建议”“拟新增”均为后续实施方案，不是已完成接入。

## 1. 不可改变的依据

当前选定分支的前端代码是功能、数据流、状态、路由、API 和交互的唯一真相源。附件和工具生成结果只能约束视觉，不能替代业务契约。

- 已确认图片与逐图排除项见[最新视觉指导](/home/hp/workspace/HpAgent_web/docs/design/frontend-visual-guide-latest.md)。三入口、HTML 成果上下文、认证字段等边界继续生效。
- 不以美化为理由移除功能、替换真实 API、伪造进度或业务数据、重写 Zustand/assistant-ui 数据源、引入整体 UI 框架。
- 第三方 skill 中的“强制”“重建”“初始化产品真相”等要求不覆盖本任务。检测器分数、通用审美规则和新生成设计图也不能覆盖已确认原图。
- 当前版本允许修改的内容不能通过本轮自己的新增代码被“自我扩充”为既有功能。

## 2. 当前 React 结构与数据流

下面是实际挂载关系的概要，不是拟议的新架构。账户和业务弹层按条件挂载；AI 内容在 Shell 中保留，空间/任务主内容按当前路由挂载。

```text
main.tsx：StrictMode → Radix Theme → App
  App：useAuth.check / sessionLifecycle
  ├─ LoginForm：登录、注册
  ├─ 会话恢复 / 连接错误与重试
  └─ AppShell（key = accountId）
     ├─ useTaskController（唯一挂载点）
     ├─ Assembly
     ├─ App Rail：AI / 空间 / 任务 / 账户
     ├─ Surface：上下文侧栏
     │  ├─ ConversationSidebar
     │  ├─ WorkspaceSidebar
     │  └─ TaskSidebar
     ├─ Main Canvas
     │  ├─ ChatPane → ConversationHeader / HpThread / 对话资料
     │  │  └─ HpThread → assistant-ui 外部 Store 适配 / 消息 / Composer / 执行展示
     │  ├─ WorkspaceScreen → FileList / 搜索 / 上传与目录操作
     │  └─ TaskScreen → TaskRow / TaskActions / 筛选与同步反馈
     ├─ InspectorHost → Surface（区域或模态）
     │  ├─ RunInspector → 资源 / 高级诊断 / Trace
     │  ├─ FileInspector → 预览 / 详情 / 版本 / 使用范围
     │  ├─ TaskInspector → 概览 / 成果与执行 / 资料 / 高级详情
     │  └─ ArtifactInspector → ArtifactPreview / 版本 / ArtifactComposer
     └─ 账户与 QQ、注册提示、任务编辑/收件箱、资料、保存、草稿保护等弹层
```

主要入口：[App.tsx](/home/hp/workspace/HpAgent_web/web/src/App.tsx)、[AppShell.tsx](/home/hp/workspace/HpAgent_web/web/src/components/shell/AppShell.tsx)、[HpThread.tsx](/home/hp/workspace/HpAgent_web/web/src/adapters/assistant-ui/HpThread.tsx)、[InspectorHost.tsx](/home/hp/workspace/HpAgent_web/web/src/components/shell/InspectorHost.tsx)。

| 状态/控制器 | 负责什么 | 视觉改造必须保护什么 |
| --- | --- | --- |
| `auth.ts`、`sessionLifecycle.ts` | 身份、能力、认证恢复；账户变更时取消请求、关闭订阅和清理所有业务状态 | 不能去掉账户 key、generation 校验和同步 reset；不能在动画结束回调里接管登录/退出 |
| `shell.ts` | hash 路由、页面记忆、唯一 Inspector、返回栈、Tabs、弹层、待确认跳转 | 保留导航序列化、请求 token、草稿/附件/授权编辑保护、返回来源 |
| `workbench.ts` | 对话、消息分页、发送/停止/重试、附件、执行状态与恢复 | 真实消息与临时进度分离；保留不确定提交、幂等意图、过期请求隔离 |
| `conversationUi.ts`、`artifactUi.ts` | 对话/成果局部草稿与呈现状态 | 不能因换布局清空草稿、串用对象状态；退出账户后需清理 |
| `workspace.ts`、`useWorkspaceQuery.ts`、`workspaceOperations.ts` | 目录/搜索、对象查询、变更、保存、授权等 | 保留取消/失效、并发版本、命令重试及授权语义，不把 Chip 动画等同于授权成功 |
| `works.ts`、`useTaskQuery.ts`、`taskOperations.ts` | 任务扫描、命令、缓存、查询与恢复 | 保留四桶语义、扫描计数、版本/控制 epoch、命令资格与原操作重试 |
| `useTaskController.ts` | 页面可见时周期同步，选择任务订阅，最多两项 Work SSE 订阅 | AppShell 中唯一挂载；侧栏/详情不重复启动控制器；不能因展示拆分增加轮询 |
| `artifacts.ts` | 消息成果、成果/版本加载、创建与修改意图 | 保留成功版本选择、失败恢复、任务原引用，不自动把新版本变成验收交付 |
| `runInspector.ts`、`trace/traceStore.ts` | Run 查询、审批、Trace/Model Input | 保留懒加载、权限/不可用状态与过期响应保护 |
| `sseClient.ts`、`runFeed.ts`、`workFeed.ts` | fetch 流式 SSE、游标、重连/降级与最终查询 | 不替换为装饰定时器或另建 EventSource；原实现有特定恢复契约 |

代码目录：[store](/home/hp/workspace/HpAgent_web/web/src/store)、[sse](/home/hp/workspace/HpAgent_web/web/src/sse)、[业务查询与操作入口](/home/hp/workspace/HpAgent_web/web/src/components)。

真实调用关系为：用户事件 → 现有组件处理函数/命令层 → Zustand 与 `HpApi`/`ApiClient` → 同源 `/api` 或 `/auth` → 响应/SSE → 原 Store → React 重新呈现。部分组件直接调用 API，不能只保护 Store 就认为业务安全。

[ApiClient](/home/hp/workspace/HpAgent_web/web/src/api/client.ts)管理同源 Cookie、CSRF、请求取消和会话代次；[HpApi](/home/hp/workspace/HpAgent_web/web/src/api/resources.ts)封装带类型的端点。修改请求保持原 `Idempotency-Key`、条件版本头和错误恢复逻辑；不在视觉层生成另一套请求身份。Vite 将 `/api`、`/auth` 代理到 `HPAGENT_API_TARGET`，默认 `127.0.0.1:8000`。

[assistant-ui runtime](/home/hp/workspace/HpAgent_web/web/src/adapters/assistant-ui/runtime.ts)使用 `useExternalStoreRuntime`，权威消息仍来自 HpAgent Store。`onNew`、`onCancel` 映射到已有发送/停止；未接入的编辑/重新生成/分支语义不能因模板中有按钮就开启。

## 3. 展示、布局与业务层修改边界

**不能把整个 `components/` 目录视为纯展示层。** 本轮核对发现，`ConversationHeader` 有重命名请求，`ArtifactMessageItems` 有可见性触发加载和生成操作，`InspectorTabs` 会写路由，`RunStatus` 包含停止/重试资格和排队计时。

| 层级 | 可做的事 | 约束/样例 |
| --- | --- | --- |
| A：可独立精修的展示切片 | 颜色、字体、间距、边框、图标尺寸、已有元素的视觉层级 | 主要落在 `styles/*.css` 和现有 JSX 的 class/装饰内容；保持标签、ARIA、事件、状态条件和数据绑定。响应式、可见性、pointer-events、布局尺寸不属于无风险修改 |
| B：受约束的布局层 | Shell 列宽、Composer 组织、Inspector/弹层几何、已有控件位置、有限视觉反馈 | `AppShell`、`Surface`、`InspectorHost`、`HpThread`、各 Screen 的 DOM 结构；必须保留节点身份、挂载时机、滚动与焦点、form/button 语义 |
| C：必须保护的业务层 | 本次工具试点不修改 | Store、API、SSE、查询/命令、能力/资格判断、幂等、版本、授权、runtime adapter、会话清理；组件内同类代码也在保护范围 |

`dateGroup.ts`、`artifactPresentation.ts` 等可包含纯计算，但“无副作用”不等于“可以改变业务语义”：状态映射、任务桶、预算、预览资格和日期分类仍须按原契约保留。真正安全的做法是按代码切片放行，不给整文件“任意改写”权限。

特别注意 [Surface.tsx](/home/hp/workspace/HpAgent_web/web/src/components/shell/Surface.tsx)：它维护原生 `<dialog>` top layer、嵌套层级、inert、焦点恢复、Portal 和稳定子树。不能用另一个 Dialog 库或 `AnimatePresence` 条件卸载重做它。HTML 成果 [ArtifactPreview.tsx](/home/hp/workspace/HpAgent_web/web/src/components/ArtifactPreview.tsx)使用 `sandbox="allow-scripts"`；不得为拾取或截图加入 `allow-same-origin`，也不应把成果内 DOM 误判为宿主 React 源码。

## 4. CSS Token、JS 几何与响应式耦合

实际样式顺序：Radix CSS → `tokens.css` → `base.css` → `shell.css` → `conversation.css` → `run.css` → `workspace.css` → `tasks.css` → `artifact.css` → `unification.css`。最后一层还有更高特异性的覆盖，不能看见基础规则就断定它生效。

| 已确认耦合点 | 证据与影响 | 建议 |
| --- | --- | --- |
| 几何 Token 未成为统一真相源 | Token 定义 Rail 64、Sidebar 256、Canvas min 560、Inspector 420/520；`shell.css` Rail/Inspector 仍有硬编码，`unification.css` 区域侧栏才使用 `--hp-sidebar` | 第一阶段冻结尺寸，仅改配色/排版。若改几何，单独提交同步修改并验证，不能宣称“改 Token 自动全局一致” |
| JS 折叠条件另写一套 | `AppShell.tsx`：`width < 960 || width < 64 + 256 + 560 + (有 Inspector 且 width >= 1280 ? 420 或 520 : 0) + 4` | 普通 Inspector 的临界点 1304px，展开后 1404px。只测 960/1280 不够；需补 1303/1304、1403/1404 |
| 容器宽度与视口宽度混用 | `useShellWidth` 用 ResizeObserver 测 Shell；CSS media query 用视口；InspectorHost fallback 用 `max-width:1279px`，AppShell 又传 `width <1280` | 在视口很宽但 Shell 被限制宽度的场景核查；不要只在最大化窗口验收 |
| 多组断点 | 599/600：底部 Rail 与全宽详情；959/960：侧栏/触控；1279/1280：详情呈现；另有上述动态折叠阈值 | 改对应布局时覆盖断点两侧，不让区域 CSS 与模态逻辑相反 |
| Theme/Portal | `main.tsx` 是 light + indigo + sage；Surface 的 Portal 包裹 Theme；token 中很多颜色引用 Radix CSS 变量 | 主题变化核对模态层与正文一致；只改 fallback 色可能根本不生效 |
| 动效/交互共用状态 | reduced-motion 同时在 CSS、Assembly、useInspectorFlow 中处理；Surface 模态切换保留子树 | 动效只消费状态；不能用视觉组件挂载/卸载驱动网络或业务生命周期 |

后续若确需统一几何来源，建议独立小任务：选择一个可供 CSS 与 JS 共用的定义源，或由 JS 读取已计算 CSS 数值；保留当前断点与行为作为第一步等价迁移，再调整尺寸。不在工具安装时顺手重构布局。

## 5. 指定工具兼容性与取舍

本机实际环境：Ubuntu 24.04.1 LTS / WSL2，Node **22.23.2**，npm **10.9.8**；已安装 React **19.2.8**、Vite **8.2.1**、TypeScript **6.0.3**、Playwright Test **1.62.1**。包版本以下为 2026-10-09 查询结果，不使用未固定的 `latest` 作为接入契约。

“声明兼容”指文档/元数据支持，不等于已在本项目通过运行验证。Vite 8.2.1 的 Node 要求为 `^20.19.0 || >=22.12.0`；项目仅声明 `>=22` 比真实工具要求宽。本机满足要求，后续环境建议明确以本机这类受支持的 Node 22 小版本为基线。

| 工具 | 官方核查结果 | React 19 / Vite 8 / TS / WSL 结论 | 决策 |
| --- | --- | --- | --- |
| Impeccable | npm `impeccable@4.1.0`，Node `>=22.18.0`；设计 skill、检测器及可选 live 模式；有 Linux 平台引擎 | 不进入 React/Vite 运行时，基本无应用 peer 冲突；不需要编译应用 TS。WSL 按 Linux 引擎运行，本机 Node 满足；引擎/浏览器联动未实测 | **先接评审能力**，禁用自动 hooks/live 写入；不做框架或产品模型初始化 |
| React Grab | `react-grab@0.2.0` peer `react >=17`，带 TS 声明与 ESM；官方 README 给出 Vite 开发态动态导入 | peer 覆盖 React 19；官方 Vite 接法存在，但未得到本项目 Vite 8/TS 6 的实测证明。依赖 React 调试信息和 bippy，源码行号映射必须试点；WSL headless 不证明 Windows 剪贴板可用 | **开发态显式开关试点**；仅元素定位与复制上下文，先不接 agent 自动编辑桥 |
| Playwright CLI | `@playwright/cli@0.1.22`，Node `>=18`；其 Playwright/Core 为 `1.64.0-alpha-1790635538000` | 在浏览器外操作，不依赖 React/Vite/应用 TS；WSL 可用 Linux 路径方案，但此版本本机未安装/运行；浏览器 revision 不应假定与现有 Test 相同 | **隔离接入**，不升级 Test，不向 web 主依赖树强行合并 alpha |
| 现有 Playwright Test | 项目实际 `1.62.1`，已有真实服务配置与 Vite-only 配置 | 已有 React 页面测试体系；本轮 WSL Chromium 启动、DOM 与 locator 检查通过。未重跑业务套件 | **保留主回归工具**，复用现有版本与浏览器 |
| VibeCurb | 核对对象为 `Yu-369/VibeCurb`，不是同名安全扫描项目；包含 `pixel-perfect`、`visual-redesign` 等 Markdown skill | 本身不是 React 运行时库，没有可验证的 React/Vite peer 矩阵；生成代码仍需 TS/业务检查。WSL 读取 Markdown 无特殊需求，安装器未验证 | **按需人工选取参考拆解方法**，不默认安装整套规则 |
| Motion | `motion@14.0.0` peer React/ReactDOM `^18 || ^19`，依赖 `framer-motion@14.0.0` | React 19 声明支持；浏览器/ESM/TS 集成合理，但本项目 Vite 8/TS 6 与布局交互未实测；WSL 不改变其浏览器行为 | **暂缓**。先用已有 CSS/有限动效，只有明确效果缺口再小范围引入 |
| Playwright MCP | `@playwright/mcp@0.0.83`，同样依赖上述 alpha；官方建议 coding agent 可优先 CLI | 与 React/Vite 无直接耦合；WSL、权限及浏览器连接仍需试点，未安装 | **暂缓**。CLI 具体能力不足且不能用已有 Test/API 完成时才评估 |

额外发现：React Grab GitHub HEAD 的 bippy 依赖为 `^0.7.2`，npm 0.2.0 元数据为 `^0.6.1`。不能拿 main 分支测试记录为已发布包背书；安装时必须固定包版本及 lockfile，并针对实际安装内容验证。

## 6. 工具风险与具体防护

这里是与本项目有关的接入风险，不是完整供应链安全认证；未审计全部发布包或传递依赖，不能声称“绝无遥测/网络”。

| 工具/入口 | 具体风险 | 接入边界 |
| --- | --- | --- |
| Impeccable installer/init | 上游安装器可写 skill/hook 配置，hook 可下载/调用平台引擎；`init` 可生成 PRODUCT.md，live 可写源码 | 指定项目范围与版本；首轮不安装 hook、不执行 init/live/polish 自动改写。把已有视觉指导和代码边界作为上下文。检查安装差异，不覆盖已有配置 |
| Impeccable 审美规则 | 通用“简化”“重设计”可能建议移除必要状态、技术详情或替换原图配色 | audit/critique 输出建议清单，逐项标注视觉问题/业务必需/原图有意排除；不机械清零检测器告警 |
| React Grab 页面脚本 | 可读页面文字、DOM、组件栈与本地源码位置；拾取/冻结/快捷键可能影响输入、Portal、iframe 和键盘焦点 | 仅本地开发、显式开启；登录密码、QQ 绑定码、真实对话与 Model Input 不采集不上传。截图和正式回归默认关闭 overlay |
| React Grab 自动安装与 agent bridge | init 可能改入口/配置；上游 CLI 有 `DO_NOT_TRACK` 检查，未设置时允许遥测 | 优先手工最小动态导入，跳过 init；如将来运行 CLI，使用 `DO_NOT_TRACK=1` 并核对该锁定版本行为。此开关不能证明其他模块无网络 |
| React Grab CDN 接法 | 运行时远程脚本、浮动版本、开发页面内容暴露面扩大 | 不使用 unpkg 脚本；本地 devDependency + 固定版本，生产构建验证剔除 |
| Playwright CLI/Test/MCP | 浏览器能执行真实页面操作，Cookie/trace/快照可能包含敏感信息；持久 profile/CDP 可能连接日常账户 | 专用临时会话与测试账户；不连日常浏览器，不复用生产 storageState；默认仅本地服务、输出到受控目录 |
| CLI/MCP 网络限制 | 官方说明 allowed/blocked origins 不是安全边界，且不覆盖所有重定向情况 | 不能把 origin 配置当隔离证明；测试进程与网络环境约束、请求拦截审计共同生效。不默认开放远程监听或 unrestricted-file-access |
| VibeCurb | 上游强调复杂网格、深对比与高级字体；部分通用规则可能与 HpAgent 的克制浅色原图冲突 | 仅借用截图提取/差异检查方法；不让它换字体系统、整体布局、引入依赖或重造页面逻辑 |
| Motion | 是真实浏览器运行时依赖，可能增加包体积；layout/exit 动画可能改变布局测量、挂载和焦点 | 只有明确场景才引入；限定装饰包装，不包管业务挂载；测量产物增量，不承诺零体积；保留 reduced-motion |

开发工具不污染生产的验收标准：生产入口图与产物不包含 React Grab/其 overlay、CLI/helper、开发配置或测试数据；生产页面不请求工具资源。**仅放入 devDependencies 不足以证明不会打包**，还必须验证导入守卫和实际产物。

## 7. 具体接入建议与改动清单

以下步骤供下一轮实施，**本轮未执行**。不需要同时引入所有工具。

### 阶段 A：隔离设计与浏览器工具

建议建立独立的 `tools/visual/` 工具包与锁文件，承载固定版本的 CLI；不改变 `web/package-lock.json` 中 Playwright Test。安装前先确认固定版本的 `--help` 与上游文档一致，尤其 Impeccable npm 包和仓库 HEAD 可能不完全同步。

- Impeccable：固定 `4.1.0` 作为试点候选；优先审阅下载到临时目录的 skill/引擎启动脚本，然后做项目级最小安装；若采用上游 installer，核对 `--scope=project`、`--providers=codex`、`--no-hooks` 等参数在该版本可用。不使用 `--force` 合并既有配置。本项目不需要它重新生成产品定位或替换视觉图。
- Playwright CLI：独立工具包固定 `@playwright/cli@0.1.22`，自身锁定依赖及对应浏览器；alpha 依赖是试点风险，不能悄悄升级原 Test。若小范围冒烟失败，继续用原 Playwright Test/API 完成浏览器操作，暂缓 CLI，而不是迁移整个测试栈。
- 两者从 WSL 内启动，仓库继续使用 `/home/hp/workspace/HpAgent_web`；不混用 Windows node/npm 与 Linux `node_modules`。headless 优先；需要可视浏览器时再验证 WSLg 与剪贴板，不提前假定可用。
- CLI 每个任务用独立会话名，关闭时只关闭该会话；不执行影响其他任务的 `kill-all`。专用 profile 不持久化登录信息，工具日志/trace/profile 加入忽略规则。

安装后 CLI 最小使用流程为：`playwright-cli -s=hpagent-visual open <本地URL>` → `snapshot` → `resize 1536 1024` → `screenshot` → `close`。可执行文件从独立工具包调用；元素 ref 失效后重新 snapshot。不把这里的交互 CLI 与现有 `playwright test` 混淆。

### 阶段 B：React Grab 最小开发态试点

预计仅改 `web/package.json`、对应 lockfile、`web/src/main.tsx`，必要时补充环境变量类型说明；不改 Store、API、路由和组件业务代码。

建议固定 `react-grab@0.2.0` 到 devDependencies，在入口增加双重守卫，示意如下（尚未写入应用）：

```ts
if (import.meta.env.DEV && import.meta.env.VITE_ENABLE_REACT_GRAB === "1") {
  void import("react-grab").catch(() => {
    console.warn("React Grab 未加载，页面功能不受影响。");
  });
}
```

按官方 Vite 接法试点，验证工具初始化时机是否能捕获 React 19 调试信息；不要为了修源码映射把异步工具加载变成主应用启动的前置依赖。开关只是开发工具启用条件，不是秘密或安全凭证。

通过条件：能从 AI Composer、Radix 控件、Portal 中的 Inspector 选中元素并定位到本仓库实际源码；页面刷新与 HMR 后不重复注册；普通复制、中文输入、ESC、Tab 和嵌套弹层无回归；关闭开关后页面无工具行为。HTML 成果 iframe 内不要求映射到宿主 React。

随后执行一次 web 生产 build 并检查产物与网络：React Grab 不进入发布包，不出现工具请求。不为工具启用生产 sourcemap 或放宽 CSP。未通过即移除动态导入/依赖和开关，回滚这一独立提交即可。

### 阶段 C：快速视觉检查入口

拟新增 `web/playwright.visual.config.ts`、`web/e2e/visual/` 下少量场景与只读回放资料；只启动 Vite，独立端口例如 5290、`strictPort`、单 worker、明确 testMatch。不继承默认配置的 backend webServer。

测试配置必须把“是否允许网络回放”“是否允许真实业务写入”分开。开发应用继续原样调用真实 API；视觉回放只存在于专用测试 BrowserContext。不得在 `src/` 中添加 mock API 开关、全局示例 Store 或伪造业务状态机。

### 阶段 D：局部视觉迭代

完成 A/B/C 的最小验证后，先选一个现有区域精修，例如 AI Composer 的间距与边框。顺序为原图/功能排除项 → Impeccable critique → React Grab 定位 → 手工小差异修改 → CLI 截图 → Test 定向回归。一次只处理一类视觉问题，保留前后截图及未解决差异。

工具试点预计文件边界：独立工具包/配置与锁文件、被审阅的项目 skill、开发工具忽略规则、React Grab 入口守卫、专用视觉测试配置及证据。**不包括** Store/API/SSE/后端/迁移，也不包括批量格式化或全仓 UI 文件覆盖。

## 8. 不依赖全量后端 E2E 的快速检查方案

现有 [playwright.config.ts](/home/hp/workspace/HpAgent_web/web/playwright.config.ts)即使只选一个 spec，也会启动真实 API、迁移及 PostgreSQL/Redis 相关环境；不能把直接执行 `npm run test:e2e` 当作轻量视觉检查。

已有 [playwright.ui7-review.config.ts](/home/hp/workspace/HpAgent_web/web/playwright.ui7-review.config.ts)仅启动 Vite，范围限制在 `ui-7-self-review.spec.ts`；[ui7-surface-harness.tsx](/home/hp/workspace/HpAgent_web/web/e2e/ui7-surface-harness.tsx)复用真实 Surface。它们是可借用的基础，不是已经覆盖五张图的视觉套件。

现有 self-review spec 包含合成 fixture 和宽泛的默认空响应；不能直接把它当作本轮真实业务证据或新增视觉数据规范。新方案禁止为凑图编造业务结果，按以下三层分工：

| 层级 | 数据与运行方式 | 能证明什么 / 不能证明什么 |
| --- | --- | --- |
| L0：纯界面与交互壳 | Vite + 真实组件，固定文本仅限测试控件/原生焦点 harness；无业务 API | 几何、字体、键盘、模态与 reduced-motion；不能证明任务/认证等业务 |
| L1：受控只读视觉回放 | 使用从测试账户真实接口记录、脱敏且有版本来源的响应；仅在测试 BrowserContext 按方法/路径精确回放 | 真实组件+Store 的既有加载呈现与截图；不能证明当前服务正确、持久化、真实 SSE 时序或权限执行 |
| L2：最终真实业务验收 | 专用测试账户、真实 API 与必要后端，仅运行受改动影响的指定流程 | 认证、授权、请求契约、持久化、幂等、恢复与业务资格。必须保留，不被 L0/L1 取代 |

L1 具体约束：

1. 不生成虚假的任务完成、预算、进度或成果内容来匹配设计图。没有合规响应记录的场景标为待采集；只验证空态/组件壳，不用手工补数据冒充业务。
2. 注册网络拦截后再导航。明确回放白名单，未知 API 请求失败并记录，不返回万能 `{items: []}`；`/auth` 和写操作默认阻止，不允许漏到 Vite 代理后的真实后端。
3. 仅允许本地应用静态资源，外网资源默认阻止并记录；禁用测试上下文 Service Worker，避免绕过拦截。记录准确的回放清单和失败请求。
4. 不通过 `window`/模块注入直接 `setState` 构造一个新业务世界，不修改 production fetch/ApiClient。必须标注截图来自视觉回放，不将它计为真实功能通过。
5. 脱敏记录排除 Cookie、真实 CSRF、密码、QQ challenge、私有资料和模型输入；需要测试会话字段时使用明确的不可用测试占位，并标记它不是认证证据。文件内容保留可公开的测试账户资料。
6. 实时 SSE/长轮询不能用固定假步骤替代。静态截图优先使用真实终态；需要流式状态时使用已记录的测试事件序列，序号/状态必须符合已有契约。实时断线恢复留给 L2。

首轮截图范围：登录/注册、AI、空间+文件 Inspector、任务+Task Inspector、AI+HTML Artifact。对应原图排除项必须保留；不增加第四导航等多余显示。

固定浏览器版本、字体、locale、`Asia/Shanghai` 时区、视口与 deviceScaleFactor；可固定测试时钟以复现日期，但不要全局暂停所有 timer 导致 SSE/业务控制器失效。等待明确 ready 条件与字体加载，不用 `networkidle` 判断长期连接页面已稳定。

快速日常视口选 1536×1024 和 390×844；改 Shell 几何时再加 599/600、959/960、1279/1280、1303/1304、1403/1404 两侧及容器宽度场景。不每次跑全矩阵。

原 PDF 是设计图片，和真实 UI 的功能内容有有意差异：用于人工构图/局部叠加比较，不直接充当 `toHaveScreenshot` 的整页基线。自动像素回归使用经审阅的真实应用截图；不得通过大面积 mask、过高容差或自动更新基线掩盖问题。截图时关闭 React Grab、live overlay，禁用/稳定装饰动画；动效本身另做有限检查。

## 9. 每阶段最小验证与退出条件

以下为后续命令建议，未在本轮执行。只跑命中变更面的检查，不执行无关全仓 CI。

| 变更 | 最小检查 |
| --- | --- |
| 工具包/skill | 固定版本、核对 lockfile/下载来源、help、仅本地空白页启动/截图/关闭；检查安装未覆盖仓库文件 |
| React Grab 入口 | web typecheck、一次生产 build/产物检查、三个组件定位、工具开启/关闭与 HMR、剪贴板和输入检查 |
| CSS 颜色/间距 | 对应场景前后截图、计算样式/溢出、基本 focus/disabled；不新增逐条复述 CSS 的单元测试 |
| Shell/Surface 几何 | 现有 Surface/shell 测试、相关断点与焦点浏览器检查，验证草稿/节点未重建 |
| 业务组件 DOM | 对应组件测试，加受影响的请求/资格/恢复检查；发现业务层改动则先缩小或拆分提交 |

在 `/home/hp/workspace/HpAgent_web/web` 下可使用的现有定向命令示例：

```bash
npm run typecheck
npm run test -- src/components/shell/Surface.test.tsx src/store/shell.test.ts
npm run test:e2e -- --config=playwright.ui7-review.config.ts --grep='R2: native modal'
```

最后一条只作为现有原生 Surface 行为的小范围检查，不是五张图的验收。每次按实际改动选择命令，不要求三条总是一起跑。长命令按仓库策略长等待，不短轮询刷状态。

最终 L2 必须按变更影响保留：登录/退出与账户隔离、发送/停止/重试、附件/资料授权、任务资格与草稿、成果版本/下载/保存、错误/无权限/断线恢复。既有默认 E2E 的 Fake Run Executor 也不等于生产模型/真实 QQ 已验收；外部投递、真实执行器等结论要单独标注实际环境，不夸大覆盖。

阶段通过条件：工具可用且可关闭、无生产泄漏、无原业务链路改写、无新增 UI 框架、截图证据可复现、测试范围明确。失败即回滚该工具的独立提交；不为修工具而放松 CSP/iframe 权限、删除功能或升级整个技术栈。

## 10. 本轮验证结果与证据局限

- 已确认当前分支、提交、已安装依赖与 WSL 环境；静态梳理了上述入口、控制器、Store、API、SSE 与样式顺序。
- 通过现有 Playwright 库启动 headless Chromium **151.0.7922.34**，只对本地 `setContent` 页面做按钮 locator 检查并关闭浏览器，未请求应用或后端。
- 已读取官方 GitHub 文档与 npm registry 元数据。React Grab 的 Vite upstream 示例文件请求超时，未将该示例视为通过证据；VibeCurb README 首次请求超时后通过 GitHub API 成功获取。
- 未安装或运行 Impeccable、React Grab、Playwright CLI、VibeCurb、Motion、MCP；它们在本项目的运行兼容性、生产剔除和源码映射仍需后续试点。
- 未验证 WSLg 可视浏览器、Windows 剪贴板、所有传递依赖网络行为；没有运行 UI 全量验收或重构页面。

元数据与环境证据见[来源快照 JSON](/home/hp/workspace/HpAgent_web/docs/design/frontend-visual-toolchain-sources-2026-10-09.json)。快照记录包版本、依赖、integrity 和观察到的上游 HEAD；README 与 HEAD 为分次读取，npm 发布包与 GitHub main 也不保证同步，不能把该快照当作完整源码认证。

## 11. 官方来源

- [Impeccable README（核查时 HEAD）](https://github.com/pbakaus/impeccable/blob/d631a8827f99414d2b6daba4ef08b7f8701751d7/README.md)：安装、hooks、init、live 与检测器。
- [Impeccable 4.1.0 元数据](https://registry.npmjs.org/impeccable/4.1.0)。
- [React Grab README（核查时 HEAD）](https://github.com/aidenybai/react-grab/blob/ea4bbec9e80f4802e8ae19ad18431edb9ddbb670/README.md)：Vite 开发态导入与元素上下文。
- [React Grab 0.2.0 元数据](https://registry.npmjs.org/react-grab/0.2.0)、[上游 CLI 遥测开关](https://github.com/aidenybai/react-grab/blob/ea4bbec9e80f4802e8ae19ad18431edb9ddbb670/packages/cli/src/utils/is-telemetry-enabled.ts)。
- [Playwright CLI README（核查时 HEAD）](https://github.com/microsoft/playwright-cli/blob/b85c7a736bb473bf55b584e54a09ffa698d6d871/README.md)、[CLI 0.1.22 元数据](https://registry.npmjs.org/@playwright%2fcli/0.1.22)。
- [Playwright 系统要求](https://playwright.dev/docs/intro#system-requirements)、[Playwright 截图比较](https://playwright.dev/docs/test-snapshots)。
- [VibeCurb README（核查时 HEAD）](https://github.com/Yu-369/VibeCurb/blob/c324ba7695a3f23229cfe9068e4cbab98a3c38f3/README.md)。
- [Motion 14.0.0 元数据](https://registry.npmjs.org/motion/14.0.0)。
- [Playwright MCP README](https://github.com/microsoft/playwright-mcp)、[MCP 0.0.83 元数据](https://registry.npmjs.org/@playwright%2fmcp/0.0.83)。
- [Vite 8.2.1 元数据](https://registry.npmjs.org/vite/8.2.1)。
