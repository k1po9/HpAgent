# R4 实施计划：对象进入 Inspector 与柔和关闭

日期：2026-10-14。状态：R4.0已完成，R4.1及R4.2.6视觉迁回版已人审接受（2026-10-15）；原计划R4.2四类对象阶段准备推进。候选交接见 `artifacts/product-acceptance/ui-iteration/R4/attempt-02/HANDOFF.md`；以下实施合同保持。

## 1. 接受基线与目标

先读 [R3 功能核查](R3-FUNCTION-AUDIT.md)、[R3 最终合同](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/INTERACTION-CONTRACT.md)、[R3 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/HANDOFF.md)、[R2 最终合同](../../../artifacts/product-acceptance/ui-iteration/R2/attempt-06/INTERACTION-CONTRACT.md)。R1 静态合同仍继承 attempt-04→05→06；用户后续明确决定优先。当前 271 文件受审指纹见 R3 DECISION，dirty 工作区才是接受版本，不能用纯 HEAD 替代。

R4 完成 B 的 Inspector 对象接力：点击一个具体对象，强化银丝说明来源与目的地；业务请求立即开始，内容只在银丝真正抵达后呈现；X 真正关闭，空白装饰壳柔和收束。最终完整体验是“发送→R3 接力→查看执行/成果→Inspector→返回或关闭→继续阅读”。

不重做 R1 材质、R2 会话/Rail/普通 Surface 与 C 六态、R3 接力。特别保留 waiting 圆环、running 菱形、状态原色、接受后即接力。Mercury 留到 R5；无后端协议/权限模型改造、全站重布局、真实消息 DOM 搬运或新业务操作。

## 2. 当前代码事实与需要补齐的接缝

| 当前代码 | 核查事实 | R4 工作 |
| --- | --- | --- |
| `components/shell/InspectorHost.tsx` | route 直接驱动标题/内容；kind:id key；onClose 和 onBack 都是 back | 分离呈现目标，X 接 closeInspector，返回保留 back，保持稳定数据宿主 |
| `components/shell/useInspectorFlow.ts` | 约 220ms 的淡入/右移，没有源点和抵达判定 | 用 B relay 替代 Inspector 旧入场，不叠加两套动画 |
| `components/shell/InspectorTabs.tsx` | 已有箭头/Home/End、ARIA 与 route tab | 添加局部约 180ms 指示条；不触发银丝或重挂载对象 |
| `store/shell.ts` | navigate 经过 dirty/附件 guard；requestToken 每次导航递增；close/back 语义已分开 | 在实际获准导航后创建呈现意图，不把每次 requestToken 变化当新飞行 |
| `components/shell/Surface.tsx` | native dialog、父层暂停、焦点恢复；普通浮层 motion opt-in | 保留协调器；Inspector 装饰退场独立，不能继续占据 modal Top Layer |
| `components/shell/AppShell.tsx` | 栏宽和侧栏折叠直接依赖 route.inspector | 逻辑关闭后仍可短暂保留纯布局占位，避免立即删列；不伪造路由开着 |
| 四类 Inspector | Run select/poll、Task refresh、Artifact load、File tree/内容请求依赖组件生命周期 | 请求不等银丝；局部提取查询或让数据宿主稳定挂载，不能双份订阅 |
| `ArtifactInspector.tsx` | 首次读取后会 navigate(replace) 自动补默认 versionId | 区分版本归一化与新对象意图，避免自动补版本造成无限取消/重发 |

额外需要测试的现存接缝：openInspector 在 navigate 后无条件 set backStack；dirtyTaskEditor 拦截时可能出现路由未变但历史栈已改。R4.0 复现并为此处增加定向回归；若确认，按“导航获准才更新历史”作最小修正，记录为本轮所需功能修复，不扩展重写路由系统。

## 3. 交互合同

### 3.1 入口与事实时钟

覆盖 Run、Artifact、File（含目录已有能力）、Task 的真实打开入口，包括主对话、Workspace、Tasks 及 Inspector 内子对象。盘点所有 openInspector 与携带 inspector 的 navigate，不能只接一张 Run 卡。

记录 kind/objectId/versionId、origin、账户/会话上下文、触发元素和意图代际。来源是实际按钮/卡片 ref 或严格身份校验的现有 triggerId，不用 nth-child 猜卡片。键盘打开同样可定位。保留源卡位置、DOM、可访问性；起终点不创建缩略卡片。

点击先按现有 guard/授权规则处理。导航获准时 route 和请求立即更新，再建立呈现 receiving。拦截时不得出现新目标选中、飞行、历史栈变更或旧面板消失。异步放行后重新验证源点、账户、目标与当前意图；源点失效直接降级。动画绝不决定权限、Run 成功或导航成功。

### 3.2 呈现时钟与抵达门控

小型 controller：closed→receiving→landed→settled；关闭走 closing。每次真正的新意图生成 token，所有 RAF、Promise、timer、transitionend 回调校验 token 与目标身份。

receiving 立即撤销旧对象可见资格；Inspector 外层标题与 aria-label、中间 Tabs、正文都使用中性接纳内容。不能只盖正文、留旧标题，不能让 opacity:0 的旧按钮仍可聚焦。通用关闭控制保持可用。数据可以在此时加载；不要为隐藏界面延迟请求或创建第二份查询。

正常飞行只有同 token 的实际路径头抵达终点才 arrive（`tipLength >= totalLength - epsilon`），不能 `setTimeout(980)` 代替抵达。抵达且视觉目标与业务目标匹配才显示当前标题、Tabs 和当前对象的内容/loading/error。慢请求不用旧对象填空。403/404、权限失效可取消装饰并立即呈现安全错误态；不展示敏感旧数据，不强迫错误等完动效。

强化银丝为主角，来源与接纳背景只做局部轻反馈；没有飞卡、没有读区粒子化。进入约 980ms 是调参起点，非固定显示闹钟；参考 B5.3/B6。同一空间坐标、真实 source/target rect，overlay 无指针事件、aria-hidden，不盖业务 modal。

### 3.3 身份、版本、Tab 与历史

- kind/id 是对象身份基础；账户、会话/区域、version/origin 中影响内容与权限的字段都参与陈旧结果防护。不能只比较 objectId。
- 同对象 Tab、expanded 改变不新发银丝，数据和 iframe 不因动画重新挂载。Tabs 保持真实 URL/ARIA/键盘，局部指示条约 180ms。
- Artifact 首次默认 versionId 归一化，在同次意图中更新解析目标，不重新发射；旧意图归一化不能覆盖新意图。用户主动选版本立即清理旧版本呈现并按新版本加载，默认作为对象内部变化，不发跨区银丝。若发生在 receiving，中性门控仍有效，抵达只能显示最新选定版本。
- 同对象重复打开不重播，保持原焦点语义。返回上一对象遵循 backStack；历史恢复/刷新无可定位来源时直接显示，不凭空发射。
- 业务历史由已通过 guard 的用户导航决定。视觉 arrive/取消回调不增删历史，也不为了“只剩 C”私自删除合法导航记录。A→B→C 的最终可见目标只能是 C。

### 3.4 X、返回与 Soft Close

X=closeInspector（结束检查器），返回=back（上一对象，无上层时关闭）。Escape 遵守当前最高层 Surface 规则。dirty guard 优先；有未保存编辑时先让原确认流程处理，不提前退场。

获准关闭后立即清除 route.inspector、旧对象可操作内容与可访问树，释放 modal 焦点陷阱并按原来源/安全回退恢复焦点。只保留不含文本、iframe、编辑器、按钮的装饰面与布局占位，约 360ms 淡化、680ms 列宽/银丝回流收束；不能保留真实旧内容截图/克隆来实现退场。

AppShell 的 inspecting class、栏宽、collapsed 判定与临时占位需协调，避免路由已关闭时右列和左侧栏先突然跳变再播放假动画。装饰占位不能让业务误认为 Inspector 仍开着。退场中打开新对象立即取消旧退出，从正确当前布局接纳新对象，旧 close timer 不得隐藏新 Inspector。

回流的源点存在且安全时才绘制；离屏/卸载则仅空壳收束。切账户、失去权限、跨会话/区域时清除敏感视图与旧来源关联，可直接终止壳；安全优先于收束完成。

### 3.5 响应式、焦点与降级

维持当前 1279/1280 modal/侧栏边界，桌面默认宽 390、展开 460。窄屏用既有 native modal；被遮挡来源不强行飞线，直接安全呈现，不伪装桌面跨区路径。不能为本轮改变已有深链接打开语义。resize/缩放更新布局、取消陈旧几何，不重新播放入口。

复用 R2 motion preference：系统或本地减动、hidden、缺 source、源/目标失联、用户滚动、几何改变、长帧/异常，直接收敛到当前业务目标或关闭状态。取消动画不是取消真实打开。所有降级必须保证不会卡在 receiving。

receiving 的焦点进入中性可读标题/关闭入口；内容不可访问。抵达不能抢走用户已经移到其他有效控件的焦点；只有仍位于接纳占位时才按需要迁移。同对象 Tab、嵌套 Surface 恢复不重新聚焦整个 Inspector。保持父层子树与未提交编辑。

### 3.6 与 R3 的组合

用户点击对象开 Inspector 优先于 A 的装饰接力。现有 R3 根据 shell requestToken 或 modal/几何变化取消，这是预期，不是回归；取消后 C 立即显示真实状态，回到对话不得补播 A。

B 和 A 独立所有者，不共享 RAF/overlay 容器，不互相调用业务发送。B 不改变 Run 生命周期、不强迫 C 回到 waiting/running；detail 始终复用实际状态映射。Surface 阻挡时取消/降级 B，普通账户/编辑浮层不发银丝。Mercury 只保留独立层级边界，不提前实现。

## 4. 实施顺序与人审

R4 一个新窗口，按子阶段顺序推进：

1. **R4.0 基线与接缝确认**：核实 accepted manifest、环境、全部入口和测试，记录 ID/version/guard/backStack/查询生命周期；完成必要业务基线。此项无需单独人审。
2. **R4.1 Run 纵向闭环**：实现最小共享 controller、Run 来源→银丝→真实抵达→详情、X 真关闭→空壳收束→重开，包含 Tab、减动、guard 和 R3 联动。共享门控若影响其他对象，必须让未接入对象安全即时呈现。自审修复后交付真实 React 的交互 HTML，停“R4.1 待人审”。用户判断空间来源、抵达同步、关闭手感；不能用运行测试代替此判断。
3. **R4.2 四类与最终组合**：局部认可后接入 Artifact/File/Task、版本/子对象返回、移动 modal、异常与中断，复验已有 Run。完成全链路与跨轮回归，交付最终交互包，停“R4 整体待人审”。用户明确接受整轮才冻结合同。

人审返修继续本窗口，attempt 递增并保留历史。没有固定返修次数；每条意见都记录假设、修改、证据和用户决定。不得将 R4.1 认可当 R4 完成；同一问题两次无改善应重审结构或提供真正不同的局部方案。

沿用用户最近“无需录像，直接 HTML 交互”的评审方式：真实 production React 单屏专注入口、相同数据前后对照、正常/慢请求/快切/关闭重开的可复现场景。场景控件仅在评审页，夹具明确标注。录像可用于定位，不作为强制人审交付；自动化事件时序、DOM/焦点断言和关键帧仍要保留。若无法实际交互检查，必须报告工具限制，不能声称已检查观感。

## 5. 工程与交付边界

沿用 React/现有 Store、Surface 和样式结构，局部改造。使用 B template 作为参考，不原样套实验比例或重建路由；不逐帧写 Zustand。取消旧 useInspectorFlow 重复入场时保留其他 R2 功能。新增纯状态与集成测试应验证真正竞态，不用镜像实现的断言凑数量。

按 [R4 验收清单](R4-ACCEPTANCE.md) 跑相关检查与生产构建。输出 `artifacts/product-acceptance/ui-iteration/R4/attempt-NN/`，包含 README、SELF-REVIEW、VALIDATION、FEEDBACK-MATRIX、HANDOFF、日志/关键帧；最终另含 DECISION、INTERACTION-CONTRACT、source-manifest 与可复现源码快照。评审包应包含登录/恢复说明，不能只有已失效端口。

历史正常产品 5294、API 8197 与 review 5317 只作线索，复用前核实进程/隔离数据与用途；不得覆盖 R3 冻结评审。账户和数据库配置从既有 HANDOFF 读取，不把默认 DSN 当隔离库。单会话账户串行测试；不外发 QQ、不调用付费供应商、不清库迁移。真实 API/Fake 与浏览器夹具分别标注。

只有准备工作在本调度窗口进行；前端在用户启动新窗口后实施。不自动提交、推送、合并、部署、创建/联系其他窗口或删除 Skill。R5 不自动启动。
