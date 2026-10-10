# 第三轮：成果卡与 Inspector 阅读布局

2026-10-10，按用户“第三轮实施”完成指导方案 F，保留第一、二轮改造。范围是现有功能的布局、呈现和样式。

## 本轮变更

- 成果卡统一图标、标题、HTML 类型、真实版本与状态的层级，增加明确的“打开”视觉入口；整张卡仍是原按钮。每个 artifact_id 保持独立，原路由、创建、查询和恢复处理保持原实现。
- 共用 Surface 增加可选的头部操作插槽，将四类 Inspector 原有的扩大阅读 / 恢复宽度按钮移入头部。原关闭、返回、模态阈值、原生 dialog、嵌套层协调和焦点处理没有改写。
- HTML 成果面板分为信息头、Tabs、中间滚动区和底部操作区。预览、版本历史、详情、任务原引用、最新版本提示、同步错误和原有修改表单位于中间；下载 HTML 与保存源码副本固定在底部。按钮仍只在所选版本具备完成的 HTML、且处于预览 Tab 时出现。
- 增大 HTML iframe 的外部可用高度，保留原 srcDoc、内容、sandbox、版本 key 和预览错误 / 重新加载实现。修改表单继续展开，可以在中间区滚动访问；没有新增折叠开关。
- 长标题显示两行，完整文字仍在 DOM、可访问名称和 title 悬停提示中；版本与状态保留独立位置。冷加载、不可用对象、失败、等待生成和运行错误继续显示当前反馈。
- 将成果相关规则集中在 artifact.css，Surface 头部与共用内边距放入 shell.css，移除 base / visual / unification 中相关的重复覆盖。

没有新增导航页、成果类型、缺失元数据或未完成能力；没有修改 API、业务 Store、SSE、权限、版本选择、生成模板及后端。

## 实际看图与修正

before / iteration1 / final 各 12 张，均为实际 React 应用渲染的页面。截图没有注入临时 CSS 或搬移 DOM。before 使用第二轮生产构建；iteration1 / final 使用本轮生产构建。

1. 阅读目标图和相关源代码，重新启动隔离服务，保存当前同屏基线。原面板的扩大阅读、标题和操作都在预览上方，手机上修改表单位于较深位置。
2. 查看第一版 1672px、1440px 和 390px 截图，确认头部操作已合并、预览起点上移，底部保存与下载可见，中间区可以滚动到修改表单。
3. 查看现有 UI7 浏览器用例的长标题截图，发现标题占满头部后，版本与状态需要额外滚动才能看到。将标题限制为两行并保留完整 title，重新查看长标题手机与 320px 短屏，确认元数据始终可见。
4. 重新构建，检查短屏、iframe 状态、修改草稿、下载、保存嵌套层与四类 Inspector，保存最终同屏图；人工查看桌面、手机、长标题、修改表单、历史、生成失败、预览脚本错误，以及空间文件与任务面板截图。

截图中，1672px 下 HTML 预览起点由约 341px 上移到 212px，预览高度由约 376px 增至 565px；底部操作区使用独立布局，没有覆盖中间区的表单。

## 对照与补充截图

| 场景 | 第三轮修改前 | 最终 |
| --- | --- | --- |
| 1672 × 941 四栏 | [before](before-artifact-1672.png) | [final](final-artifact-1672.png) |
| 1440 × 900 四栏 | [before](before-artifact-1440.png) | [final](final-artifact-1440.png) |
| 1366 × 900 | [before](before-artifact-1366.png) | [final](final-artifact-1366.png) |
| 1280 × 900 | [before](before-artifact-1280.png) | [final](final-artifact-1280.png) |
| 1024 × 900 模态 Inspector | [before](before-artifact-1024.png) | [final](final-artifact-1024.png) |
| 390 × 844 成果面板 | [before](before-artifact-390.png) | [final](final-artifact-390.png) |
| 390 × 844 AI 页面 | [before](before-ai-390.png) | [final](final-ai-390.png) |
| 空间 | [before](before-workspace-1672.png) | [final](final-workspace-1672.png) |
| 任务 | [before](before-tasks-1672.png) | [final](final-tasks-1672.png) |
| 新对话 | [before](before-new-ai-1672.png) | [final](final-new-ai-1672.png) |

[目标图](../ui-target-20261010/target.png)、[第一版](iteration1-artifact-1672.png)、[长标题修正前](review-long-title-before.png)、[长标题最终](check-fixture-long-title-390.png)、[320 × 480 短屏](check-short-320x480.png)、[手机修改表单](check-composer-mobile.png)、[历史](check-history-desktop.png)、[详情](check-details-desktop.png)、[嵌套保存弹层](check-save-layer-mobile.png)。

真实隔离 API：[文件面板](check-file-1672.png)、[手机文件面板](check-file-390.png)、[任务面板](check-task-1672.png)、[手机任务面板](check-task-390.png)。浏览器读取夹具：[等待生成](check-fixture-queued-mobile.png)、[正在生成](check-fixture-running-mobile.png)、[生成失败](check-fixture-failed-mobile.png)、[指定版本不可用](check-fixture-missing-mobile.png)、[权限不可用](check-fixture-denied-mobile.png)、[预览脚本错误](check-fixture-runtime-error-mobile.png)。

## 数据与检查边界

复用前两轮隔离数据库 hpagent_visual_20261009、Redis DB 14、测试文件目录，以及同一视觉检查账户、对话、HTML 成果、文件和任务。before / final 使用同一组消息与 HTML 记录、相同滚动位置；没有替换原 HTML 内部排版。新对话截图只打开空白界面。

现有浏览器测试使用独立测试账户，包含实际 API 创建、修改、源码副本上传与保存，也包含明确标注的网络夹具。补充视觉检查中的状态、长标题和错误仅通过浏览器读取响应构造，未写入数据库或接入产品入口；下载检查把所选 HTML 保存到忽略的临时目录，逐字核对原内容。没有运行正式模型、QQ 通信或计划任务实际执行。

空间的“视觉检查记录.md”在隔离数据库有元信息，其临时存储 blob 已缺失，正文 API 返回“服务暂不可用”。文件面板截图保留真实错误和重试入口，本轮只确认该状态及元信息的布局，没有据此宣称文件正文读取成功，也没有修改后端或替换文件内容。

## 验证结果

- 7 个相关组件测试文件、40 项通过：成果卡、成果 Inspector、自检恢复、iframe 预览、Surface、Inspector 流程和 App。标题修正后复跑其中 2 文件、14 项，仍通过；14 项是原 40 项的子集。
- 11 项相关 UI6 / UI7 浏览器用例通过。首批 10 项通过，四类 Inspector 键盘用例因入口转场尚未释放焦点而提前按键；在该用例中补充等待目标解除 inert、入口消失的同步后，该用例单独复跑通过。没有修改生产转场或降低键盘断言。最后标题修正后复跑长标题与跨宽度相关 2 项，均通过。
- 浏览器覆盖幂等创建 / 修改恢复、历史版本保护、草稿、任务原引用、冷加载与同步恢复、失效对象、保存丢失响应和名称冲突、iframe / 草稿 / 查询次数跨断点保持、嵌套保存与任务弹层、焦点返回、四类 Tabs、扩大阅读、reduced-motion 和字号放大。
- 最终构建补充记录 41 组几何状态：17 个普通宽度（320–1672px），4 组扩大阅读，4 组短屏（含 844 × 390），预览与修改表单的滚动，真实文件 / 任务桌面与手机面板，以及状态 / 长标题 / 错误夹具。页面无横向溢出，头部、Tabs 和底部操作没有越出面板；滚动前后底部位置稳定。Surface、iframe、修改框的 DOM 身份、草稿和 iframe 内交互状态保持。[结果](visual-regression.json)没有页面运行错误。
- 下载内容与当前所选版本 HTML 相同；保存取消后草稿、iframe 和按钮焦点恢复。多个成果保持不同按钮 ID。
- typecheck、生产 build、变更文件 ESLint / Prettier、git diff --check 通过。构建仍有已有的大 bundle 提示。

[基线尺寸](before-geometry.json)、[最终尺寸](final-geometry.json)、[状态检查](visual-regression.json)。日志位于本目录；e2e.log 保留首次失败，e2e-keyboard.log 记录同步修正后通过，e2e-refinement.log 记录标题修正后的相关回归。

三轮布局与样式范围已实施。目标图中的第四个导航、照片在线状态、逐步进度、Markdown 成果和不存在的操作仍按指导方案边界处理，未实现新功能。
