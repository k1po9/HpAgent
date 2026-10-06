# UI-2 自检问题修复指导

> 日期：2026-10-07（Asia/Shanghai）。状态：已执行，R1–R3 复验通过，见[实施报告的修复复验记录](ui-2-ai-interaction-report.md#自检发现与修复复验)。
> 本文最初作为待修复指导编写，以下复现与根因描述保留修复前状态；最终实现及验证证据以实施报告为准。

## 1. 基准与修复目标

依据 [UI-2 实施计划](ui-2-ai-interaction-plan.md)、[实施报告](ui-2-ai-interaction-report.md) 与当前未提交的 UI-2 实现。Git HEAD 为 `cc4086273a92ea269abe863b5a054bf5026dcbb1`，但问题存在于其上的工作区改动，不能仅检出该 HEAD 复现。

自检确认原报告的 161 项单元测试、29 项 E2E 和 2 项后端契约测试有保存日志支撑；本轮聚焦复跑原有测试 12 项通过，新写的 3 个复现用例全部失败。这些数字属于不同批次，不相加、不改写原验收日志。

| 编号 | 优先级 | 问题 | 主要文件 | 影响验收项 |
| --- | --- | --- | --- | --- |
| R1 | P1 | 撤销实际成功但响应丢失，读回空规则后渲染崩溃 | `ConversationResources.tsx` | U2-11/12 |
| R2 | P2 | 撤销期间切换对话，返回后残留 busy 状态 | `ConversationResources.tsx` | U2-11/12、U2-14 |
| R3 | P2 | A 的发送请求未返回时，B 的发送被共享锁静默阻止 | `HpThread.tsx` | U2-06、U2-14 |

修复目标是恢复异常场景下的可操作性，并保持账户隔离、同一意图幂等、同一对话防重复发送及稳定 runtime 挂载。

允许修改上述组件、对应测试及必要的局部状态接线。保留当前未提交改动，不重置工作区。不修改后端权限协议、Run 状态机、SSE 或 Artifact 协议，不借修复扩展 UI-3/UI-4 范围。

## 2. R1：读回无剩余授权时关闭撤销确认

### 2.1 复现与根因

1. 对话 A 有一个或多个长期授权规则，用户打开撤销确认并提交。
2. DELETE 已在服务端成功执行，但客户端未收到响应，Promise 被拒绝。
3. 随后的 GET 返回 grants，原目标规则均已不存在。
4. 当前实现根据 `failed > 0`，把过滤结果 `[]` 传给 `setRemoving`。
5. 空数组为 truthy，确认弹窗继续渲染，并读取 `removing[0]!.name`，抛出 `TypeError`。

源码入口：[ConversationResources](../../web/src/components/conversation/ConversationResources.tsx)。当前风险点是 `setRemoving` 对空数组不归一化，以及弹窗用 `removing &&` 判断却直接解引用首项。不能用 TypeScript 非空断言证明运行时存在元素。

### 2.2 推荐修法

以读回的服务端状态判断是否仍有待撤销规则，不以 DELETE Promise 的失败数判断最终授权状态。

```ts
// 示意：targetRules 是提交时冻结的规则集合。
const targetIds = new Set(targetRules.map((rule) => rule.grant_id));
const remaining = page.grants.filter((rule) => targetIds.has(rule.grant_id));
setRemoving(remaining.length > 0 ? remaining : null);
```

同时在状态入口把 `[]` 归一为 `null`，并在渲染前检查首项存在，避免未来其他调用再次制造非法状态。两个防线都应有明确语义，不通过 ErrorBoundary 吞异常作为修复。

| 读回结果 | UI 行为 |
| --- | --- |
| 原目标规则全部不存在 | 清除该次撤销错误，关闭确认弹窗，刷新 Chips，显示“授权已撤销” |
| 部分规则仍存在 | 弹窗仅保留这些规则，提示剩余数量，重试只请求仍存在的 grant_id |
| 全部规则仍存在 | 保留选择，显示尚未完成及可重试反馈 |
| 读回失败 | 显示“状态待确认，请重新查询”，保留必要上下文；下一次恢复先查询，再决定需要撤销哪些规则 |

若 DELETE 响应丢失，不掌握其 `affected_runs`，则只能根据 grants 证明授权已撤销，不能宣称所有受影响 Run 已停止。可沿用现有 Run 快照刷新，停止状态仅来自真实响应或快照。

### 2.3 必加测试

在 [ConversationResources.test.tsx](../../web/src/components/conversation/ConversationResources.test.tsx) 加入：

- mock DELETE 先从模拟服务端 grants 移除规则，再抛网络异常；GET 返回空 grants。断言无渲染错误、弹窗关闭、显示无长期资料。
- 多条撤销有成功、有响应丢失，读回只剩一条；断言只展示、只重试该条。
- DELETE 和读回均失败；断言不显示全部成功，查询恢复后按真实剩余规则继续。

首项复现可用测试专用 ErrorBoundary 捕获渲染崩溃，但必须断言 fallback 未出现；不能只断言 dialog 消失，因为组件崩溃同样会让 dialog 消失。

## 3. R2：分离视图写入资格与撤销操作清理

### 3.1 复现与根因

1. A 发起撤销，记录 `busyFor = A` 和当前查询 generation。
2. 请求未返回时通过浏览器历史等入口切换到 B，generation 变化。
3. A 请求结束，旧 token 不再匹配，结果写入和 `finally` 清理都被跳过。
4. 返回 A 后，`busyFor === id` 再次成立，“使用资料”“确认撤销”持续禁用，弹窗关闭也被 busy 条件阻止。

该问题已通过延迟 Promise 和 A→B→A 组件测试复现。查询世代用于阻止旧数据写入新视图，但操作自己的锁仍须释放；两者不能共用同一个清理条件。

### 3.2 推荐修法

为每次 mutation 设置唯一 operation token，并按账户会话与 conversationId 保存进行中操作。查询 generation 继续保护当前视图内容，操作 token 负责匹配和清理自己的 busy 状态。

建议局部维护按主体索引的操作记录，而非一个 `busyFor` 字符串。记录至少能区分：当前账户会话、conversationId、本次 operation token。使用 state 或显式订阅通知驱动按钮状态，不能仅改 ref 却不触发重渲染。

```ts
// 示意：实际可用局部 state + ref 实现同步去重与响应式展示。
const owner = captureAccountSessionAndConversation();
const token = beginOperation(owner);
try {
  const result = await revokeAndReadBack(owner, frozenRules);
  if (canWriteCurrentView(owner, queryGeneration)) applyResult(result);
} finally {
  // 释放操作资源与能否更新当前视图是两个判断。
  finishOperationIfTokenMatches(owner, token);
}
```

必须保证：

- A 的 finally 可以释放 A 自己的操作记录，即使用户正在 B。
- A 的旧 finally 不能清掉 B 的锁，也不能清掉 A 后续新操作的锁。
- 同一主体仍有进行中撤销时禁止重复确认；切回来不能凭切页动作提前解锁。
- 切换主体后清理或隔离旧确认弹窗、错误和通知；回到 A 重新查询真实 grants，不继续使用旧的待撤销规则。
- 如果返回 A 时请求仍进行，完成后要使 A 的查询失效或触发读回，否则提前发起的 GET 可能留下旧 Chips。
- 账户退出/更换和组件卸载后使旧视图写入失效。账户 A→B→A 也不能让旧操作更新新会话，不能只比较 accountId 字符串。

不要简单删除 `finally` 的 token 判断并无条件 `setBusy(false)`；那会让旧 A 响应解除 B 或新 A 操作的锁。也不要在 conversationId 改变时把全部操作记录清空，让同一未完成撤销得以重复提交。

### 3.3 必加测试

在 `ConversationResources.test.tsx` 用 deferred Promise 精确控制顺序：

1. A 撤销 → 切 B → A 完成 → 返回 A：资料入口可用、无不可关闭的 busy 弹窗。
2. A 撤销 → 切 B → 返回 A → A 完成：完成前不能重复确认，完成后恢复并显示真实授权状态。
3. A 撤销 → B 发起自己的撤销 → A 完成：B 仍保持 busy，直至 B 自己完成。
4. 旧查询 generation 失效：旧操作可以清自己的记录，但不能覆盖新视图的 grants/error/notice。
5. 账户重置或卸载后旧请求结束：不回灌旧主体内容。

## 4. R3：按对话隔离 Composer 提交锁

### 4.1 复现与根因

1. 对话 A 输入并发送，将 onSend Promise 保持未完成。
2. HpThread 不卸载，切换到已加载且空闲的 B。
3. B 输入文字，发送按钮可用，点击后没有第二次 onSend 调用。

源码入口：[HpThread](../../web/src/adapters/assistant-ui/HpThread.tsx)。`submitting` 是组件级 boolean ref；UI-2 为保留 runtime 而保持组件挂载，因此 A 的锁继续阻止 B。可见按钮依赖 store 的当前对话状态，submit 却额外读取全局 ref，产生“看似可发送但无响应”。

### 4.2 推荐修法

将 UI 防连点锁按 `conversationKey` 隔离，并为每次提交分配独立 token。示意：

```ts
const submissions = useRef(new Map<string, symbol>());

async function submit() {
  const owner = conversationKey;
  if (submissions.current.has(owner) || otherSendGuards()) return;
  const token = Symbol("submit");
  submissions.current.set(owner, token);
  try {
    await sendCapturedIntent();
  } finally {
    if (submissions.current.get(owner) === token) {
      submissions.current.delete(owner);
    }
  }
}
```

此代码仅说明锁的所有权；实际实现需保留 IME、当前 Run、sending/stopping、附件 ready、文本非空等既有判断。按钮状态与当前主体的提交锁保持一致；如用 ref 同步挡住连点，需配套可渲染状态或沿用有明确保证的 store 状态。

补充约束：

- B 可独立发送，不等 A 的网络请求完成。
- A→B→A 且 A 请求仍未结束时，不能在 A 重复发送。
- A/B 同时有未决请求时，A 完成只释放 A；A 失败同样不能改变 B 的状态。
- 发送前冻结草稿所属 key、revision 和内容；成功只清对应版本，不能清 B 或 A 的更新草稿。
- 空态 `account:new` 创建真实 conversationId 时，明确锁的交接：创建窗口由既有 creating 锁保护，真实对象发送由 sending/命令层保护，不能利用 key 变化重复提交同一意图。补首次创建集成测试证明交接无空窗。
- 账户生命周期仍沿用 UI-1/2 的统一 reset 与 generation 保护；若改用共享锁 store，必须注册到同一清理边界。
- 保留 Workbench 的幂等 key/payload、未知结果确认、临时消息替换与 feed 去重。UI 提交锁不能替代命令幂等，也不能把未知结果重试变成新意图。

不要通过给 HpThread 添加 `key={conversationKey}` 强制重挂载来清锁；这会破坏本阶段稳定 runtime、阅读位置与既有生命周期保证。不要仅在切对话 effect 中把共享 boolean 重置，否则旧请求 finally 可能释放新提交。

### 4.3 必加测试

扩展 [HpThread.ui2.test.tsx](../../web/src/adapters/assistant-ui/HpThread.ui2.test.tsx)：

- A 请求 pending，rerender 同一个 HpThread 为 B，输入并发送；断言 onSend 调用两次且参数分别属于 A/B。
- A pending 时重复点击 A，只产生一次调用；A→B→A 仍不能重复发送 A。
- A/B 均 pending，先完成 A，再尝试重复发送 B；B 的锁仍有效。
- A 迟到成功不清 B 草稿，也不清 A 更新后的 revision；拒绝/异常分支会释放自己的锁。

扩展 App 集成或浏览器测试，延迟 A 的真实发送响应，选择 B 后完成一次发送，验证 API 请求归属。仅组件 mock 不能证明首次创建、路由与 Workbench 接线正确，首次空态发送与原 key 确认也需复跑。

## 5. 执行顺序与验证

1. 重新记录 HEAD、dirty diff，核对问题函数仍与本文一致；保留已有用户改动。
2. 把 3 个最小复现场景加入正式测试，保存修复前失败结果。
3. 在同一资料组件内完成 R1/R2，先跑资料定向测试；再完成 R3，跑 Composer/store/App 相关测试。
4. 为所有新锁补交错完成与账户重置测试，确认不是用放宽防重或强制卸载换取通过。
5. 执行静态检查、全量单测及受影响浏览器回归，最后更新验收报告。

定向测试在 `web/` 下执行：

```bash
npx vitest run src/components/conversation/ConversationResources.test.tsx src/adapters/assistant-ui/HpThread.ui2.test.tsx src/store/ui2.test.ts src/store/workbench.test.ts src/store/sessionIsolation.test.ts src/App.test.tsx src/App.recovery.test.tsx
```

最终前端检查：

```bash
npm run typecheck
npm run lint
npm run build
npm test
```

浏览器回归至少运行：

```bash
npm run test:e2e -- e2e/ui-2-ai.spec.ts e2e/conversation.spec.ts e2e/multi-tab.spec.ts e2e/auth.spec.ts e2e/workspace-p1.spec.ts e2e/workspace-p3.spec.ts
```

给 `ui-2-ai.spec.ts` 增补 DELETE 成功后响应丢失、撤销期间浏览器历史切换、A 请求延迟时 B 发送的场景。浏览器测试继续使用 [测试指南](../development/testing.md) 要求的隔离数据库、Redis、文件目录和串行 worker；不直接复用业务库，不为通过用例删除业务 Work。

本次修复不改变后端契约；权限读回与请求范围若发生额外变化，再选择受影响后端契约测试复跑。测试期间冻结被验证源码；耗时命令使用合理的长等待，不为检查进度反复启动或轮询。

## 6. 实施报告修订与退出标准

在原 [实施报告](ui-2-ai-interaction-report.md) 追加“自检发现与修复复验”章节，保留原批次结果。修复前应明确当前存在 R1–R3，不能仅保留覆盖表而使读者误判异常场景已完整验收。

报告至少补充：

| 记录项 | 要求 |
| --- | --- |
| 缺陷状态 | R1/R2/R3 分别列待修复、已修复待验证、复验通过；状态由实际结果决定 |
| 影响范围 | U2-11/12 加响应丢失后的空读回；U2-14 加跨对话 mutation/提交锁；U2-06 加并发发送恢复 |
| 基准 | 修复后的 HEAD 或未提交 diff 标识、实际修改文件清单 |
| 验证证据 | 修复前失败及修复后结果、实际命令、退出码、运行环境、日志路径 |
| 计数 | 分批记录，新增用例按最终实际数量统计，不把历史通过数与重跑相加 |
| 仍有限制 | 实机软键盘、真实 IME、原生文本缩放、完整人工无障碍及真实模型等原限制继续保留 |

退出标准：3 个原始复现用例均通过；R2/R3 的交错完成测试证明旧操作不会解锁新操作；同主体防重、原 key 确认、草稿保护、账户隔离及稳定 runtime 的旧测试继续通过；规定检查和受影响 E2E 有本轮证据。

自检临时复现源码和日志曾保存在 `/tmp/hpagent-ui2-review-resources.test.tsx`、`/tmp/hpagent-ui2-review-composer.test.tsx` 及同名前缀 `.log`。这些文件不属于项目持久证据，可能被系统清理；可作为编写正式用例的参考，本文的复现步骤不依赖其持续存在。将测试迁入上述正式测试文件后，应把本轮脱敏日志保存至 `artifacts/product-acceptance/ui-2/` 下独立的修复复验目录，不覆盖原验收日志。
