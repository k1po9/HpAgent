# UI-6 自检与修复记录

> 2026-10-08（Asia/Shanghai）。状态：R1/R2/R3 三个 P2 已修复；原始自检与失败证据保留，下方追加独立修复复验。
> 审查对象为当前未提交的 UI-6 实现，以及 [实施报告](ui-6-html-artifact-report.md) 和 [验收证据](../../artifacts/product-acceptance/ui-6/README.md)。HEAD 为 `9560c99675d6ebfd159909ea9d812790572c6a1d`，不能仅用 HEAD 表示被审代码。

## 原始自检结论与范围

实现证据清单中 35 个文件的 SHA-256 全部与当前工作区一致。定向复跑已有测试 10 文件、105 项通过；另外编写 4 个反例，全部在原自检时的实现失败，归为下面 3 个 P2 问题。

主要检查了版本查询/轮询/命令缓存、默认选择、消息摘要、草稿与幂等、源码保存接线、Task 验收边界和预览隔离。没有发现本轮可确认的 P0/P1；这不代表完整产品验收通过。未重跑全套前端、真实数据库/HTTP、Chromium、真实模型或 Temporal。原报告的 353 项等计数属于原实施批次，不算本次执行结果；A6-19/A6-20 的人工及真实手机未覆盖边界继续保留。

## R1 · P2：跨查询通道缺少逐版本新旧保护，已完成状态可退回生成中

**位置：** [artifacts.ts 的 poll 回写](../../web/src/store/artifacts.ts#L190) 与 [loadArtifact 合并](../../web/src/store/artifacts.ts#L234)。

**触发与实测：**

1. v1 当前 running，单版本 poll GET 已发出但响应延迟。
2. 新发起的版本列表 GET 先返回 v1 completed，缓存可正常预览。
3. 旧 poll 返回 running，被 `cacheVersion` 无条件 upsert；反例读到 running，而非预期 completed。

第二个反例覆盖另一路：列表 GET 等待期间只有 v2 poll 写入，Artifact 级 `writes` 发生变化。列表随后带回 v1 completed，但实现将缓存中的**所有**版本覆盖回列表，因此未更新过的旧 v1 running 也会覆盖新完成状态。

**用户影响：** 已出现的 HTML 预览、下载/保存动作消失，版本重新显示生成中，修改也可能被构建锁挡住；下一次查询成功前持续错误，断网时影响更久。账户 generation 与对象 epoch 只能防跨账户/失效对象回灌，不能解决同对象列表与 poll 的响应先后。

**修复建议：** 为同一 artifact_version_id 统一合并规则和请求世代；列表快照不能因为另一个版本写入而整体让缓存胜出。保留列表请求发出后新创建的版本，同时避免旧 queued/running 覆盖已确认终态。对合法的服务端重试转换按真实契约处理，不能只用粗糙全局状态排序。poll 应感知版本缓存已终态，避免继续拿旧局部状态回写。

**复验要求：** R1 与 R1b 两个反例通过，并保留现有“POST 新版本在旧列表返回后不丢失”“多构建去重”“权限失效停止轮询”语义。对应 A6-04/A6-08/A6-09/A6-10。

## R2 · P2：相同版本号的旧摘要优先，刷新仍显示生成中

**位置：** [artifacts.ts 的 loadForMessage 合并](../../web/src/store/artifacts.ts#L298)。

**触发与实测：** 首次读取消息成果返回 v1 running，之后服务端完成 v1；再次强制读取消息列表返回 v1 completed，但 `latest.version >= item.latest_version.version` 使旧 summary 胜出。无需并发或乱序，连续两次已完成的 GET 就可复现。反例确认第二次请求确实发出，最终摘要仍为 running。

**用户影响：** 消息中的 HTML 对象行持续显示生成中。页面恢复后只加载消息列表、尚未打开 Inspector 的场景没有相应版本 poll 来纠正它；即使调用强制刷新也无法接受同一版本的完成/失败状态。普通非强制调用还会直接返回已缓存列表。

**修复建议：** 区分“版本号更新”和“同一版本状态更新”。同号时按请求新旧与已知终态合并，不能无条件信任旧摘要；缓存中真正更高版本仍需保留。明确消息列表发现 queued/running 后的有界刷新/轮询所有权，确保恢复页面后对象行能够收敛，不依赖用户先打开详情。

**复验要求：** R2 通过；补同号 running→failed、新 GET 完成但旧消息请求迟到、已缓存更高版本不被低版本摘要覆盖，以及页面恢复仅显示对象行时的状态收敛。对应 A6-01/A6-04/A6-09。

## R3 · P2：无指定版本重开时，默认选择先于本次加载完成

**位置：** [ArtifactInspector.tsx 默认版本 effect](../../web/src/components/artifact/ArtifactInspector.tsx#L44)。

**触发与实测：**

1. 内存已有 v1 completed，query.loading=false；另一个标签页已产生新的成功版本。
2. 用户点击消息对象行（不带 versionId）重开 Inspector，load effect 开始 GET 并同步设 loading=true。
3. 同一轮渲染的默认选择 effect 仍持有旧 loading=false、旧版本列表，将 v1 写入 Shell URL。
4. GET 返回 v1 与新成功版后，effect 因已有 versionId 退出；反例最终选中 v1，而非最新成功版（fixture ID v2，真实 version 数值为 8）。

**用户影响：** 用户没有主动选择历史版，却默认打开旧内容，并可能下载/保存旧版。新版本提示可以手工补救，但不满足无显式版本时默认最新成功版的约定。这与“用户主动选历史版后不抢焦点”不同，不能以保护历史选择解释。

**修复建议：** 将首次默认选择绑定本次对象加载的成功结果/request token；区分用户明确选择与初始化选择。加载前可展示缓存，但不要提前把缓存版本固化为显式 URL 选择。期间若用户手选、离开或切账户，初始化结果不得反向抢占。

**复验要求：** R3 通过；同时验证首次冷加载、显式历史深链、加载中手选、网络失败保留缓存、A→B→A 和关闭后的迟到加载。对应 A6-03/A6-04/A6-10。

## 自检证据与后续门槛

独立证据目录：[ui-6/self-review](../../artifacts/product-acceptance/ui-6/self-review/README.md)。

| 本次检查 | 结果 |
| --- | --- |
| 原实施文件哈希 | 35/35 一致 |
| `git diff --check` | exit 0 |
| 既有定向回归 | 10 文件、105 项通过，exit 0 |
| Store 新反例 | 3 项失败，exit 1；对应 R1、R1b、R2 |
| Inspector 新反例 | 1 项失败，exit 1；对应 R3 |

反例使用真实当前 store/组件与受控 API 响应顺序，属于 Vitest/jsdom 级验证；没有声称已在浏览器或生产环境重现。临时反例已从 `web/src` 移出，以 `.txt` 形式保留在证据目录，避免把预期失败项混入正常全量测试。原业务代码、测试、日志及截图未被覆盖。

修复时将反例归入正式回归，确认 4 项由失败转为通过，再执行相关 Artifact/Shell/消息/Workspace/Task 回归及项目要求的完整前端检查。对浏览器覆盖补无版本重开与断网恢复；更新报告 A6-03/A6-04/A6-09/A6-10 的结论，不能只复用原自动化通过记录。


## 2026-10-08 修复结果

R1：列表、消息摘要与逐版轮询共用 GET 发出序号，按 artifact_version_id 决定响应的新旧；移除 Artifact 级 writes 的整体覆盖。旧列表仅保留发出后写入的额外版本，已接受的新 POST 不丢失。completed 是不可变 HTML 事实，旧 running 不得覆盖它，等待中的 GET 也可以确认完成；序号不会倒退。failed 不做永久终态锁：依照 `src/web_artifacts/build.py::_prepare`，同一 Run 的重试可以在同一版本将 failed 改为 running，并清 completed_at；较新的 GET 仍接受这一转换。没有引入虚构的 attempt 字段或全局状态排序。

R2：消息摘要中的同版本状态先经过统一版本缓存合并，再选真实最高版本；顺序强制 GET 可接受 completed/failed，旧消息响应不能覆盖较新失败状态。消息列表发现 queued/running 后接管逐版轮询，最多四个单版本 GET 并发，沿用每版一个 poller 与退避，不依赖打开 Inspector。poll 在等待后与拿到并发名额后都检查缓存终态；账户 reset 独立清理并释放等待队列，对象失效 epoch 阻止旧消息重新灌入已清缓存。

R3：默认选择直接绑定本次 `loadArtifact` 的成功 Promise 结果，移除读取旧 render loading 的初始化 effect。默认写 URL 前检查组件仍挂载、Shell requestToken、当前对象以及未发生明确版本选择。重试沿用相同初始化逻辑；失败时保留缓存 HTML 预览而不把缓存版固化成 URL 指定版。显式历史版、等待时手选、A→B→A、关闭和账户切换均有正式回归。

原 4 个反例整理为正式 [Store 回归](../../web/src/store/UI6SelfReview.test.ts) 与 [Inspector 回归](../../web/src/components/artifact/UI6InspectorSelfReview.test.tsx)，修复前再次确认 4 失败，修复后全部通过，并增补 16 个边界场景。浏览器新增 [自检修复场景](../../web/e2e/ui-6-self-review.spec.ts)，以真实 API 创建的 HTML 为起点，注入有序/失败 GET 来验证无版本重开及仅消息行恢复；不把合成 v8/v9 描述成真实后端生成结果。

修复批次完整命令、退出码、日志、4 张新截图及源码哈希见 [fix 证据](../../artifacts/product-acceptance/ui-6/self-review/README.md)。计数独立于原实施与原自检批次。未改后端契约，未复跑原 Python 契约批次；真实模型、Temporal、手机软键盘、200% 文本/完整读屏人工验收仍未覆盖。A6-19/A6-20 的部分覆盖结论保持。

### 修复批次最终验证

| 检查 | 独立结果 |
| --- | --- |
| TypeScript / lint / production build | 全部 exit 0 |
| Artifact/Shell/消息/Preview/Workspace/Task 定向 | 12 文件、125 项通过，含全部 20 个正式修复场景 |
| 全量 Vitest | 48 文件、373 项通过，exit 0 |
| Chromium 跨模块直接 CLI | 9 spec、30 项通过，exit 0；包含定向浏览器的 2 个新场景，不叠加计数 |
| 文件与提交检查 | 38 个源码/测试哈希、文档链接、diff whitespace 校验通过 |

首轮跨模块浏览器同样 30 项通过，但执行包装器在服务收尾后返回 143；该批次未列为命令 exit 0，保留完整日志。随后直接 exec Playwright CLI，取得独立 exit 0。原 UI-4/UI-5 生成截图和原 UI-6 20 张截图恢复原副本，仅保留 fix 目录下 4 张新截图。
