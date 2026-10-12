# HpAgent Phase 1 · Paper Flame 交付

2026-10-12。默认主题符合 Paper Flame；生产包已构建，当前停在 Phase 1。未发布、未合并到 main，未推进 EXP-02。

- 原分支 / commit：main / a47d73b06a993d353b4177e282b69ddd28ffdb36。
- 实施分支：codex/phase1-paper-flame。
- 代码提交：419125121faf40893d0133b60e5fd93b8d1c1434。
- [实际源码及文档 diff](code.diff)。运行 `git diff a47d73b..4191251 -- web docs/implementation/phase1-paper-flame` 可复查；交付截图与报告在后续 evidence commit。
- [实施前逐文件审查](../../../docs/implementation/phase1-paper-flame/AUDIT.md)；[Mercury 兼容与 EXP-02 决策](../../../docs/implementation/phase1-paper-flame/MERCURY.md)。

## 改动与 Token 迁入

| 范围                                 | 结果                                                                                                                                                                             |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 统一色板                             | tokens.css 是唯一颜色定义处，官方 `--pf-*` → 兼容 `--hp-*`；组件 CSS 已消除色值硬编码，粒子仅保留缺失 Token 的两项 fallback                                                      |
| 四区                                 | Rail #16212C / Context #EDF1F5 / Canvas #F8F9FB / Inspector #F4F6F8；阅读面、卡片、输入区白色，用户气泡 #EBF1F5                                                                  |
| 品牌 / 操作                          | #EB6044 用于品牌与细小图形；白字主按钮/小字链接 #B94025；hover #A53620 是可访问性补充。#CF482D 官方参考色保留在色族中                                                            |
| Radix                                | 主入口改 tomato 基础；明确映射 accent ramp、gray 文本与控件、panel/background；tomato 本身不冒充精确 Paper Flame。嵌套 Theme/Portal 同样消费 Token，red/green/amber 显式状态保留 |
| Conversation / Composer              | 中性 AI marker，用户冷灰气泡，白阅读区，清晰 placeholder、控件边界与焦点；发送/停止继续共用原控制；草稿、IME、运行及附件处理未改                                                 |
| Run / Trace / Task                   | 普通执行状态冷灰，成功绿、危险红、警告琥珀；TaskRow 只增加来自现有 status/bucket 的 data 属性；Run 两个 warning Text 从 orange 改 amber。修正既有 Run 按钮重复选择器             |
| Workspace / Artifact / 登录 / Dialog | 文件图标中性、下载原生蓝链接修正、输入边界和主按钮统一；Artifact 外围统一，用户 HTML iframe 内容未换色或改写；浅灰 Inspector 与白阅读卡保持独立                                  |
| Entry / Motion                       | 登录粒子仅读取 silver-neutral 与 brand-hot Token，fallback 更新；GSAP 时间轴、轨迹、数量、缓存及清理均保留                                                                       |

`--hp-border` #D4DCE3 用于装饰分隔；`--hp-control-border` #7B8994 用于需要辨识的输入/按钮。小字号 muted #5D6F7B 对 Context 4.60:1；白字主按钮 5.49:1；控件边界对白 3.59:1、对 Context 3.16:1。Rail hover 与焦点采用深底专用 Token，焦点通过实际 computed style 核对为 #FFD9CB。

四区尺寸 Token、responsive 阈值、AppShell/ChatPane/HpThread/Surface DOM 结构、API、Store、导航 guards、消息/Run/Conversation/Workspace 语义保留。Run 基础按钮样式修正属于本轮必要外观统一。

## 真实前后截图

39 对同视口、同记录的正式 React 页面截图；另有 1 张最终 Rail 焦点图。before 来自原始 commit 的独立源码快照；after 来自本轮 `web/dist` 的生产 preview。没有临时换 CSS、移 DOM、复制实验 HTML 或注入 Store。

| 页面 / 状态                            | 修改前                                  | 修改后                                |
| -------------------------------------- | --------------------------------------- | ------------------------------------- |
| AI / Conversation · 1680×1000          | [before](before-ai-1680.png)            | [after](after-ai-1680.png)            |
| 新对话 · 1680×1000                     | [before](before-new_ai-1680.png)        | [after](after-new_ai-1680.png)        |
| AI + Artifact Inspector · 1680×1000    | [before](before-artifact-1680.png)      | [after](after-artifact-1680.png)      |
| AI + Run Inspector · 1680×1000         | [before](before-run-1680.png)           | [after](after-run-1680.png)           |
| Workspace · 1680×1000                  | [before](before-workspace-1680.png)     | [after](after-workspace-1680.png)     |
| Workspace + File Inspector · 1680×1000 | [before](before-file-1680.png)          | [after](after-file-1680.png)          |
| Tasks · 1680×1000                      | [before](before-tasks-1680.png)         | [after](after-tasks-1680.png)         |
| Task Inspector · 1680×1000             | [before](before-task-1680.png)          | [after](after-task-1680.png)          |
| 登录 · 1680×1000                       | [before](before-auth-1680.png)          | [after](after-auth-1680.png)          |
| AI · 390×844                           | [before](before-ai-390.png)             | [after](after-ai-390.png)             |
| Artifact 模态 · 390×844                | [before](before-artifact-390.png)       | [after](after-artifact-390.png)       |
| 文件模态 · 390×844                     | [before](before-file-390.png)           | [after](after-file-390.png)           |
| 任务模态 · 390×844                     | [before](before-task-390.png)           | [after](after-task-390.png)           |
| 注册验证错误 · 390×844                 | [before](before-register-error-390.png) | [after](after-register-error-390.png) |
| 资料弹窗                               | [before](before-resources-modal.png)    | [after](after-resources-modal.png)    |
| Composer 键盘焦点 / 草稿               | [before](before-focus-draft.png)        | [after](after-focus-draft.png)        |

所有上表页面另有 1280/780/390 的截图，命名 `before/after-{scene}-{width}.png`。[Rail 焦点补充](after-focus-rail.png)。

人工读图覆盖桌面四区、三大页面、登录/注册错误、手机输入区、四类 Inspector；初版采样混入过渡或空 iframe，修正同步后重采，当前只保留稳定最终画面。历史对话冷恢复不显示已终态的实时 Run strip 是现有行为；通过真实 Run Inspector 核对记录，未改其业务含义。

## 验证结果与边界

- **typecheck：通过**，`tsc --noEmit`；`tsc -b` 初次生产构建也通过。
- **lint / format：通过**，对实际修改的 TS/TSX、脚本、配置和 UI7 用例执行 ESLint；对所有组件 CSS 与上述文件执行 Prettier。没有执行全仓 `npm run lint` 或完整 CI。[静态检查日志](static-checks.log)。
- **单测：15 文件 / 104 项通过**，App/恢复、HpThread、Conversation Header/Sidebar、RunStatus、Surface/Inspector flow、Task Integration/SelfReview、Workspace SelfReview、Artifact Preview/Inspector/MessageItems、LoginForm。命令使用 `node node_modules/vitest/vitest.mjs run` 加以上对应测试文件；没有运行完整单测集。
- **生产 UI：6 项通过**，[日志](e2e-final.log)：真实粒子像素与清理、reduced-motion、切换动效偏好；Artifact iframe/草稿/查询次数跨 13 个尺寸和边界、扩大阅读、200% 字号；手机嵌套保存 Escape/焦点；四类 Inspector 的键盘 Tabs、URL replace、几何。
- **开发 harness：2 项通过**，[日志](e2e-harness-final.log)：native modal 的键盘候选与背景禁用；desktop nonmodal 的背景导航与 Escape 返回。harness 是源文件，不在生产 dist；误在 preview 执行曾失败，之后改在 Vite 5293 单独复验，不据此声称生产支持 harness。
- **真实 UI 截图脚本：通过**，[日志](visual-final.log)：39 对 + 1 焦点图，真实 API 数据、四种宽度、页面导航、草稿保留、注册验证、文件读取与 HTML iframe 内容；没有页面运行错误。
- **渲染色 / 对比：通过已采样范围**，1409 个可见文本组合（包含跨视口重复项），0 个低于对应 4.5/3:1 的组合，0 个遗留高饱和蓝文字；[最终实测](after-geometry.json)、[基线](before-geometry.json)、[比较](comparison.json)、[Skill 色板计算](palette.log)。扫描按实际 computed style 合成透明背景，排除 disabled 文本、屏外文本、用户 iframe；并非全站 WCAG 认证。人工确认控件与焦点，常规搜索/Composer 边界约 3.59:1。
- **布局：通过已测范围**，39 对截图中 Rail/Context/Main/Inspector/Composer 的 x/y/width/height 对比无变化，页面无横向溢出；尺寸为 1680×1000、1280×1000、780×1000、390×844。
- **build：通过**，[日志](build.log)；已有 >500kB bundle 警告保留，未在本轮进行代码拆包。
- **基线测试问题：已修正同步**，[分析](baseline-draft-failure.md)：原基线和初版都在仍 inert 的登录过渡中填写草稿；仅给既有用例增加等待，原基线与最终版本都通过。没有为通过测试改产品动画或草稿逻辑。

环境：Linux Node 22.23.3（复用已安装 Docker runtime）、现有锁定依赖；Windows Node 24.14.0 调用 Playwright / 本机 Chrome 155.0.8059.39。WSL 缺少浏览器运行时且安装源不可达，改用本机 headless Chrome；不将结果扩展为 Firefox/Safari/真实移动设备验证。

真实隔离 API 8196 / DB `hpagent_phase1_20261012` / Redis DB 15；仅测试环境迁移、账户、文件与未来时间提醒；消息、HTML 是现有 FakeRunExecutor/FakeArtifactExecutor 的测试内容。截图账户 `phase1_visual`，浏览器回归账户 `alice` 均属于隔离数据库。正式模型、QQ 通信、Temporal 计划实际执行、所有权限边界、未出现在截图中的错误与实时组合、Mercury 整段性能均**未测**。没有访问日常业务账户或更改生产数据库。

## Mercury 与 EXP-02

本轮仅提供 neutral/hot/core/trail/reflection/depth 光学 Token 与真实 DOM/状态契约文档。白底下 #FFF3E9 高亮与 #C5D0D8 反射对比不足，须将来用局部轮廓/密度验证，不能把整个页面改暗。阅读 viewport 含 Run/Artifact 控件，Composer 为兄弟节点；收纳范围必须先产品决定。

EXP-02 建议优先：Conversation 条目与宿主反馈 → 阅读表面/消息边界 → Composer 发送/停止 → Run 与 Artifact 卡 → Inspector Header/Tabs → Workspace 行与 Tasks 状态。先决定收纳单位、新消息/运行中行为、入口、无可见宿主退化、per-conversation 展示态与 reduced-motion，再开始运动实现。[详细契约与待决策](../../../docs/implementation/phase1-paper-flame/MERCURY.md)。

未复制 P09 HTML，未收纳整个 HpThread，未增收纳按钮、路由、业务折叠状态或 Mercury 控制器。可用 `git revert 4191251` 回退主题实施（证据提交单独保留），不会迁移或回退业务数据库。

## 实际代码 / 文档文件

- `docs/implementation/phase1-paper-flame/AUDIT.md`
- `docs/implementation/phase1-paper-flame/MERCURY.md`
- `web/e2e/ui-7-unification.spec.ts`
- `web/playwright.phase1.config.ts`
- `web/scripts/phase1-contrast.mjs`
- `web/scripts/phase1-visual.mjs`
- `web/src/components/RunStatus.tsx`
- `web/src/components/entry/particles.ts`
- `web/src/components/tasks/TaskRow.tsx`
- `web/src/main.tsx`
- `web/src/styles/artifact.css`
- `web/src/styles/base.css`
- `web/src/styles/conversation.css`
- `web/src/styles/entry.css`
- `web/src/styles/run.css`
- `web/src/styles/shell.css`
- `web/src/styles/tasks.css`
- `web/src/styles/tokens.css`
- `web/src/styles/unification.css`
- `web/src/styles/visual.css`
- `web/src/styles/workspace.css`

## 重现入口

在已单独准备的隔离 API 上运行（Windows/WSL 需 WSLENV 传递相关环境变量）：

```sh
# 本机已有 Chrome 可通过 HPAGENT_BROWSER_EXECUTABLE 指定
HPAGENT_PHASE1_ISOLATED=1 HPAGENT_PHASE1_PHASE=after node web/scripts/phase1-visual.mjs
cd web
HPAGENT_PHASE1_ISOLATED=1 node node_modules/@playwright/test/cli.js test --config=playwright.phase1.config.ts --grep 'seven viewports|mobile saving|all four Inspectors|valid cookie|reduced motion|changing motion'
# native modal / desktop nonmodal 需 Vite dev，不能指向生产 preview
HPAGENT_PHASE1_ISOLATED=1 HPAGENT_PHASE1_BASE_URL=http://127.0.0.1:5293 node node_modules/@playwright/test/cli.js test --config=playwright.phase1.config.ts --grep 'native modal|desktop nonmodal'
```

截图脚本默认只读已保存 routes 中的隔离记录；首次准备可用 HPAGENT_PHASE1_SEED=1（登录现有测试账户后通过真实 UI 创建数据），首次注册还需 HPAGENT_PHASE1_REGISTER=1。不能指向日常业务环境。

附件 SHA256：Paper Flame `54d1fc5a57e36c031aa393db334ff9a4f45b9c443c8f98728b86fb830e2b552e`；Mercury `3790f90899c05677bcf6e7bcbfecec469dc22a413aeeecb5d7a908291c3f45a5`。附件实验图仅用于规范参考，没有作为验收截图。
