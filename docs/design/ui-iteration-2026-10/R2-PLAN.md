> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# R2 计划：空间反馈、执行生命状态与输入微状态

日期：2026-10-13。状态：启动资料已准备，尚未实施。本文是新实施窗口的工作合同，不是已完成效果报告。

## 1. 起点与结果

R1 已经由用户正式接受，见 [最终决定](../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/DECISION.md) 和 [交接](../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/HANDOFF.md)。本次准备资料时，已逐文件核实最终 source-manifest 的 208 个文件与工作区一致。基线为 `main@b8a5a02eb83b4e122367e321040e78e1aae7cd3e+dirty`，源码指纹 `a32a65e6c18ce16031fda8f860f21aa6c5d157b52fb59f0c361fe834f1609eaa`。实施窗口必须重新核实，不能把 Git HEAD 当作已接受成品。

R1 解决静止时的阅读、材质、对象身份、任务四区和账户面板。R2 解决操作发生时的连续性：我切到了哪里、系统正在做什么、输入何时可提交。创新来自空间、颜色和动作有稳定含义，不靠到处发光或增加动画数量。

| 用户操作 | 当前起点 | R2 目标效果 |
| --- | --- | --- |
| 切换会话 | 选中样式变化 | 选中项轻微归位，有落座感，不挪动整列 |
| 切换主要分区 | 路由与选中态变化 | 单一活动轨迹连续移动，目标内容轻落定 |
| 打开账户/普通浮层 | 原生 Surface 已具备层级与焦点 | 面板抬升入场，关闭立即释放交互，空间归属清楚 |
| 查看 Run | 多种非终态共用 Spinner | 六种状态有独立颜色和形态，正文与详情讲同一事实 |
| Run 快速换阶段 | 文本/Spinner 更新 | 文本立即正确，视觉从当前画面连续交接，不闪灰重启 |
| 输入、准备发送 | 静态 Composer 和按钮 | 冷瓷聚焦、可发送内焰、按钮意图接力；不会误示已发送 |
| 中断或减动 | 分散处理 | 立即回到当前业务事实，不补播旧动画、不残留装饰层 |

## 2. 必须继承的静态合同

完整读取 [attempt-04 合同（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R1.3/attempt-04/STATIC-DESIGN-CONTRACT.md`）、[attempt-05 合同（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R1.3/attempt-05/STATIC-DESIGN-CONTRACT.md`）、[attempt-06 合同](../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/STATIC-DESIGN-CONTRACT.md)，后者及用户最新决定优先。不得只读旧 R1 S1 候选。

- 现有 Rail 104、Context 280、Inspector 390（展开 460）、阅读宽度 790；维持既有响应式断点和手机底部导航，不套回旧布局数字。
- 回答统一银瓷面 `#E7E9EC`，正文、执行与成果关系保持；主背景与已接受的微暖材质保持，不把所有对象重新改成同权重白卡。
- Composer 保持单层瓷面，无内部下划线、无工具竖隔线；保留可见键盘焦点和按钮触控面积。
- RunInspector 保持用户可读标题，完整 Run ID 留在高级诊断。
- 任务默认 B：四区同时可见，左侧桶定位/强调，不改回单桶过滤。焦点/忙碌卡保留位置，实际状态和计数立即更新；按现有规则离开焦点后才搬动。不为动画重排卡片，不改变原生 disabled。
- 账户默认 A：保留现有 Surface 尺寸、内容和 QQ 回调/轮询。不得以测试为由发送真实 QQ 消息。

## 3. 范围与实现约束

### B：日常空间语法

Conversation Re-seat、Rail Shift、普通 Surface Elevate 是本轮范围。会话约 220ms；Rail 单一 3px 活动轨迹，内容落定约 280–360ms；Surface 入场约 310ms（Y 14px、scale .986），退场约 210ms。这些是起始参数，按原速读图微调并记录原因，不能改变 Skill 的动作身份。

选择变化以实际通过导航/脏编辑 guard 的目标为准。被拦截不能先显示目标已选中。轨迹读取实际几何，移动端按横向导航适配；resize 更新几何，不重播切换。保留会话 DOM、草稿、滚动与 iframe。

Surface 保留 native dialog、top-layer 和已有层级协调器。明确 opt-in 的普通浮层范围，避免全局选择器误伤 Inspector。关闭逻辑、焦点释放、敏感内容清除立即发生；若保留退场装饰，必须无交互、无正文、无焦点陷阱。嵌套时仅顶层接收 Escape，父层暂停不销毁子树，返回父层不误当首次入场。

Inspector 银丝、抵达门控、Soft Close、X/返回语义调整和 Tab 接力留到 R4。现有 Inspector 动效不要被新 Surface 动效叠加。

### C：六态生命信号

建立纯事实映射与独立展示层，复用到 RunStatus/ExecutionBlock 的 compact 和 RunInspector 的 detail。compact 32–42px，detail 68–90px；不是把大图等比例塞入小位置。

| 视觉态 | 颜色 | 动作起点 |
| --- | --- | --- |
| running | `#2879BF` | 环，4.1s |
| thinking | `#7C57CC` | 三点 1.6s、背景 2.3s |
| tool | `#008E91` | 节点 1.4s |
| waiting | `#B46F12` | 慢呼吸 3.1s |
| succeeded | `#18835E` | 一次 560ms 后静止 |
| failed | `#CF3F4A` | 一次 460ms 后静止 |

映射优先级：终态 > cancelling > queued/waiting > running 下可信审批/资源等待 > 可信模型阶段 > 工具阶段 > 通用 running。cancelling/cancelled 使用中性表达，不伪装成失败，也不新增后端状态。`finalizing` 不等于成功，SSE 退化不等于失败；过期 phase 必须失效。颜色之外保留状态文字、形态和必要动作，不展示虚构思维链或进度百分比。

状态事实、文本、停止/重试资格立即更新。视觉颜色从屏幕当前值约 520ms 连续过渡；旧形态约 360ms 退出，新形态约 55ms 延迟后 470ms 进入。快切始终最新意图获胜，旧图层数量有界，不通过灰色或 key remount 重置。重复终态事件不重播；历史终态首次打开默认静止，仅本次实际观察到的终态跃迁播放一次。

RunProgress 的可信性依赖 runId、账户/订阅代际和 freshness，不能只看 phase 字符串。历史 Run 和 Work Run 没有阶段事实时保持诚实。任务四桶是 Work 状态，不整体套用 Run 六态颜色。

审批区分 unknown / known-none / known-pending。现有审批查询依赖 Inspector 资源组件生命周期，不能假定主对话始终知道审批。优先复用已有可信数据；支持不足则标明限制，不为每条消息新建查询/订阅，不擅自扩后端。保持原有业务 Store 边界、请求和订阅所有权。危险副作用不能因为红色失败态而获得盲目重试按钮。

### A：仅输入微状态

Idle/focus 使用冷瓷和上沿冷银反射，保留已接受的可访问键盘焦点。只有草稿真正可提交且附件就绪，底部才有局部内焰细缝。Send hover 或键盘意图出现时，细缝退亮、按钮提亮轻抬，约 280–340ms，不出现双峰。触屏不能残留 hover。

上传中、上传失败、提交资格不足不得表现为 ready。提交中与确认未知保持中性，不假装后端已接受。IME、按会话提交锁、幂等确认和 revision 保护的草稿清除沿用现有逻辑。发送/停止模式仅由业务事实决定。发送飞线、渐冷轨道和 C 捕获留给 R3；本轮 C 直接呈现真实运行态，不等待不存在的接力。只保留必要的稳定环目标 ref/几何接口。

### 共用的减动与生命周期

统一解析系统 `prefers-reduced-motion` 与本地用户选项 `system | reduce`，系统要求减动时始终优先，不提供强制覆盖系统的动画档。可在现有账户 A 中加入小型“减少动态效果”设置，不重做面板、不加后端字段；优先复用已有设置机制，实施前核实。

减动保留颜色、形态、位置和语义，取消持续旋转、呼吸、位移等装饰运动。偏好改变、hidden、账户/Run/路由代际变化、卸载都清理本轮控制器；回到页面直接显示最新事实，不补播历史。resize 更新布局，不重新入场。不要顺带重构登录 EntryTransition。

可用 CSS/WAAPI/小控制器；不引入大型动画库或接管业务的总状态机。不逐帧写 Zustand。装饰 SVG aria-hidden、不可拦截指针。循环只在可见且需要时运行；失败可关闭视觉层，业务必须继续正确。

## 4. 代码落点（实施时再次确认）

| 现有位置 | 工作 |
| --- | --- |
| `web/src/components/ConversationSidebar.tsx` | 实际导航成功后的 Re-seat |
| `web/src/components/shell/AppShell.tsx` | Rail 活动目标、普通 Surface 入口、偏好接入 |
| `web/src/components/shell/Surface.tsx` | 有范围的 Elevate，保持层级/焦点合同 |
| `web/src/components/RunStatus.tsx` | 替换通用 Spinner，保留业务文字与操作谓词 |
| `web/src/components/run/ExecutionBlock.tsx` | 当前 Run 事实适配，历史不批量查询 |
| `web/src/components/run/RunInspector.tsx` | detail 复用映射，保留 live/快照边界 |
| `web/src/adapters/assistant-ui/HpThread.tsx` | Composer 微状态，保持提交业务路径 |
| 既有领域样式与局部新模块 | token、运动、控制器和语义测试；避免另加全局最终覆盖层 |

建议小型 vitality 映射/呈现模块、spatial 控制器、input-action 微状态和共享偏好。文件名由实施窗口按仓库习惯决定；不先全站搬家。

## 5. 顺序、反馈与停止点

R2 使用一个新实施对话，子阶段在该对话顺序完成，不并行修改前端。

1. **R2.0 基线**：读取输入、核实源码与隔离环境，记录静态图和原速操作，列明真实数据缺口。无需为了例行检查再次请求许可。
2. **R2.1 B 空间反馈**：实现三个日常动作，完成自审、反例修复和原速录像；提交可操作预览，停在 B 局部人审。至少让用户判断落座、轨迹、浮层是否克制且清楚，不能只问“喜欢吗”。
3. **R2.2 C 生命状态**：B 获认可后实施六态、双尺度、连续切换及降级；提交六态与快速连续变化的人审包，停在 C 局部人审。
4. **R2.3 A 与整体**：C 获认可后完成输入微状态和组合打磨，检验同屏多个运动中心是否争抢注意力；用户实际体验完整链路后明确接受整个 R2。

每次人审写明当前子阶段，不把局部认可升级为整轮通过。收到否决后在本阶段返修，保存问题→假设→修改→复验→用户决定；不要固定凑次数。若同一审美问题两次返修未改善，重新检查设计假设，提供有实质区别的局部方案。只有真实取舍时才做 A/B，不重新选整站皮肤。

每次交付前代理必须实际看截图和原速视频，记录具体时间点/画面问题及修复；如果工具不能读视频，逐帧抽样检查并明确原速观感尚未验证，不宣称已看。自动化与自评分不能保证审美满意，只能保证检查与反馈可追溯。

最终实际链路：输入/附件 → ready → 发送按钮意图 → 真实提交 → queued/running/阶段 → 停止或终态 → 详情 → 返回继续输入，再串联会话切换、任务和账户。这里没有 R3 飞线和 R4 银丝。

## 6. 证据与环境

按 [R2 验收矩阵](R2-ACCEPTANCE.md) 执行。资料放在 `artifacts/product-acceptance/ui-iteration/R2/attempt-NN/`，每个 attempt 记录 stage，保留失败证据。每阶段提供 README、SELF-REVIEW、VALIDATION、FEEDBACK-MATRIX、HANDOFF；最终另附 DECISION 和 R2 的交互合同。

生产预览历史地址 5294、API 8197、数据库 `hpagent_r1_20261013`、Redis DB14 等仅是交接线索。先验证进程、端口、账户和数据隔离，复用前确认用途，不覆盖别的窗口。历史 5307/5308 也不能默认仍可用。隔离账户见 R1 HANDOFF；单会话限制下浏览器测试串行。不得连接默认 DSN 做迁移/清理，不发送真实 QQ、不调用付费供应商来凑证据。

真实 API、确定性事件注入和静态实验分开标注。注入可证明竞态呈现，不证明真实供应商覆盖。预览必须是项目实际 React 页面；Skill HTML 只能作参考。最终提供生产构建可操作版本和源码指纹，不能只有开发模式演示。

不修改后端协议，不提交/推送/合并/部署，不删除 Skill 或覆盖 R1 证据。未提交的 R1 改动属于用户；禁止 reset 到 HEAD。新 worktree 若缺这些未提交输入应先说明，不能在旧版本上继续。
