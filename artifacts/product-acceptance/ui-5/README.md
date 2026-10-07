# UI-5 验收证据（2026-10-07）

> 2026-10-08 后续 R1–R3 自检修复见 [修复证据](self-review/README.md) 与 [修复记录](../../../docs/implementation/ui-5-self-review.md)。下文数量及截图为原实施阶段证据，保留历史记录。

> 2026-10-08 补充：[UI-5 自检记录](../../../docs/implementation/ui-5-self-review.md)确认 3 个待修复 P2。本轮 [已有回归](self-review/regression.log) 80 项通过，[反例](self-review/repro.log) 3 项失败；复用的 [反例源码](self-review/UI5SelfReview.repro.test.tsx.txt)与原实施验收分开保留。以下为原实施批次记录。

对应 [实施报告](../../../docs/implementation/ui-5-task-center-report.md) 与 [实施计划](../../../docs/implementation/ui-5-task-center-plan.md)。基线为 `be57e0bd22623974ce57498aed78c086fc5f6673`；开始时已有计划文件及索引修改。实现和本批测试清单见 [changed-files.txt](changed-files.txt)。历史 UI-2/UI-3/UI-4 截图保留，本批 32 张回归截图复制到 `regressions/`。

## 最终命令与结果

| 验证 | 实际命令/范围 | 结果与日志 |
| --- | --- | --- |
| TypeScript | `cd web && npm run typecheck` | 通过，[typecheck-final.log](logs/typecheck-final.log) |
| ESLint/Prettier | `cd web && npm run lint` | 通过，[lint-final.log](logs/lint-final.log) |
| 生产构建 | `cd web && npm run build` | 通过，[build-final.log](logs/build-final.log)，保留 >500 kB chunk 提示 |
| 完整前端单测 | `cd web && npm test -- --maxWorkers=1 --testTimeout=15000` | 42 文件、316 项通过，101.56 秒，[unit-final.log](logs/unit-final.log)。310 项是最后检查前一轮，保留 [unit-before-conflict-review.log](logs/unit-before-conflict-review.log)。 |
| 后端既有契约 | 下方三个 Work API/domain/persistence 文件；`.venv/bin/python` | 37 项通过，66.39 秒，1 条弃用提示，[backend.log](logs/backend.log) |
| Chromium | 下方八个 spec，workers=1，真实 API/PG/Redis、Fake Executor | 框架报告 28 项全部通过，5.6 分钟，[e2e-final.log](logs/e2e-final.log)；临时父进程清理退出说明见下文 |

浏览器完整命令（先配置隔离测试环境）：

```bash
cd web
npm run test:e2e -- e2e/ui-5-tasks.spec.ts e2e/manual-repair.spec.ts e2e/ui-3-execution.spec.ts e2e/ui-4-workspace.spec.ts e2e/artifact.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts
```

后端完整命令（仓库根目录，先配置隔离环境）：

```bash
PYTHONPATH=src .venv/bin/python -m pytest test/web_api/test_work_foundation.py test/work_domain/test_foundation.py test/web_persistence/test_work_integration.py -q
```

使用专用可丢弃 PostgreSQL `hpagent_ui5_contract_20261007`、`hpagent_ui5_e2e_20261007`（前三轮）、`hpagent_ui5_e2e_final_20261007`（最终全新库），migration/API/worker 三角色连接同一测试库，Redis DB11，API8185/Vite5278，文件目录 `/tmp/hpagent-ui5-files-20261007`。未清理业务库、未停止现有服务、未安装依赖。凭据由本机现有服务配置读取，不收录；Playwright trace 含测试登录及正文，留在忽略的临时 test-results，不纳入证据目录。

## 失败与复验记录

- 后端首轮 [backend-environment-initial.log](logs/backend-environment-initial.log) 缺少迁移目录，第二轮 [backend-auth-initial.log](logs/backend-auth-initial.log) 使用了与运行实例不同的默认测试密码，均在 fixture 初始化失败。补齐本机隔离环境配置后，37 项真实契约通过；失败日志不当作领域结果。
- [unit-targeted.log](logs/unit-targeted.log)：定向 60 通过、controller 一项失败；测试状态 reset 保留了被还原的 mock 方法，修复夹具恢复后通过。
- [unit-full.log](logs/unit-full.log)：288 通过、8 项原 App 恢复测试失败，因为旧手工 Work/Run 查询入口被删除。将能力移到折叠高级入口后，原断言全部保留，下一轮 [unit-full-final.log](logs/unit-full-final.log) 304 全部通过。
- [unit-before-metadata-fix.log](logs/unit-before-metadata-fix.log)：306 通过、1 项新增要求元数据回传测试失败，证实完整 GET 行不能直接用作 POST 要求。修复只发送九个领域字段后，310 项门禁通过；这轮不代替修复后的证据。
- [e2e-initial.log](logs/e2e-initial.log)：23 通过、4 失败。真实修订因回传数据库记录元数据得到 422，已修复；旧 `.hp-work-panel` 断言迁移到任务 Screen；时间断言统一为同一 instant；51 条 API 种子补齐 Origin/CSRF 和 UUID 幂等 key。
- [e2e-recovery.log](logs/e2e-recovery.log)：定向 9 通过、2 失败。预算测试按 `.first()` 选到了 JSONB 排序后的其他维度，改为模型 Token 的可访问名称定位；API 登录后同文档 hash 导航未重新触发前端鉴权，分页种子结束后显式 reload。保留原业务断言。
- [e2e-reused-db.log](logs/e2e-reused-db.log)：26 通过、2 失败。创建/修订/预算/控制全程成功，最后修订事件断言同时命中可见段落和折叠 JSON，改为精确事件文案。Artifact 用例在复用库中 15 秒未产生 iframe，未改弱断言；只读检查 Work Run 存在 4 queued、1 succeeded，最终使用全新可丢弃库复跑。queued 数不能单独证明根因，只作为隔离环境复验依据。
- 最后检查补充了 409 界面反例（服务端错误形状的 stub）：TaskEditor 的恢复回调也用于冲突刷新，原代码刷新后关闭草稿。改为只有返回成功 Work 才关闭；新增要求/预算冲突保留输入与新基线提交，以及独立重复投递风险确认测试。[integration-review.log](logs/integration-review.log) 的 10 项通过；随后 [query-isolation.log](logs/query-isolation.log) 的 3 项通过，验证去重/三路限制、换报告/账户迟到响应、网络保留与 403 清除。新增错误夹具遗漏 HpErrorDetail 的必填字段导致一次 typecheck 失败，补全后复跑最终门禁，保留 [typecheck-review-initial.log](logs/typecheck-review-initial.log)。

最终 Playwright 已输出 `28 passed`，临时外层 wrapper 随后以 143（SIGTERM）退出；该退出码单独保留，不能称外层命令为 exit 0。将 npm 子进程放入独立 process session 后，用既有 a11y 两项检查复验测试服务器启动/清理，2 项通过、外层 exit 0，见 [e2e-teardown-isolation.log](logs/e2e-teardown-isolation.log) 和 [退出码记录](logs/command-exits.txt)。28 项指框架已完成的测试数，不与这两项补验相加。

各轮通过数不相加；结果均为当前批次单独测量。

## 操作轨迹与证据性质

**成功：** 真实 API 创建上海时间 2027-01-01 09:00 的提醒 → 读回 instant 为 01:00Z → 修改目标/填写原因 → 暂停 → 仅提高 model_total_tokens 1000，API 读回其他限额不变 → 恢复 → 明确确认停止 → 已结束桶可见、普通修订不可达 → 高级详情可读 r2 修改原因。既有 manual-repair 另验证研究要求修订与 Work 目录授权。

**响应丢失恢复：** Chromium 将真实 create POST 先 `route.fetch()` 提交，再丢弃响应 → 显示响应未知并保留输入 → 重试相同 key → 真实列表读回仅一个同名 Work。资料沿用 UI-4 用例：上传 PUT 丢响应只恢复保存，保存后 grant 失败只重试授权，不重新上传或自动创建新的权限。

**冲突/并发/迟到：** 同 Work 双击只发一次，不同 Work 可并发；网络未知用原 payload/If-Match/key；已提交但 GET 失败不再提交。409 要求和预算草稿保留，审阅最新 baseline 后使用新版本与新 key。旧账户 mutation、列表、报告、通知不能写回新对象；已有 Workspace 正文 A→B→A 反例也随完整前端回归通过；关闭 TaskEditor 后成功快照保留但不导航；Budget Dialog 关闭后迟到成功不关闭新弹窗。控制器在 StrictMode 与隐藏/恢复条件下最多两个 Work feed，慢扫描不重叠。

**真实状态约束：** Python 三文件在真实 PostgreSQL 中验证跨账户、CAS/并发 winner、pause/stop 控制收敛、Run 成功不等于 Work 完成、revision/epoch fence、明确 input 撤销、版本预算、uncertain 投递责任、旧回执不能完成新要求、Artifact provenance/验收以及显式 QQ 目标。QQ 案例为契约/模拟 sender，不是实际 QQ 发信或 Temporal 运行。

**合成界面：** M01–M21 与 UI-5 network-fixture 的四桶/pausing 决策使用完整契约形状的合成快照；UI-3 原有网络夹具验证审批/诊断。它们证明投影、入口、文案和布局，不作为真实领域收敛证据。Research Markdown 与文件独立失败在 jsdom API stub 中验证，不宣称真实研究模型生成过报告。

## 分页、截图和界面检查

Store 用 101 项合成数据验证第三页待办、失败 cursor 恢复、重复游标保护、首页刷新保留后页及单调版本；真实浏览器在独立新账户创建 51 项未来提醒，读完第二页后第 1 条仍可见，刷新后不丢失，attention 筛选外深链可打开。通知 100+ 翻页与去重是单测夹具；没有伪造服务器全局数量。

任务列表/Inspector 开关覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080，共 14 张；另含 reduced-motion、200%文本、不可用任务及真实分页。四桶、pausing 决策、空筛选另保存桌面和手机合成截图。

- [桌面列表](tasks-1440x900.png)、[桌面 Inspector](task-inspector-1440x900.png)
- [手机列表](tasks-390x844.png)、[手机 Inspector](task-inspector-390x844.png)
- [200%文本](task-text-200-percent.png)、[reduced-motion](task-reduced-motion.png)、[不可用对象](task-unavailable-mobile.png)
- [51 项分页](tasks-paginated-desktop.png)
- [需要处理](fixture-bucket-attention-1440.png)、[进行中](fixture-bucket-active-1440.png)、[等待](fixture-bucket-waiting-1440.png)、[已结束](fixture-bucket-ended-1440.png)
- [暂停中确认投递](fixture-pausing-decision-390.png)、[空筛选](fixture-filter-empty-390.png)

浏览器断言横向不溢出、Tabs 键盘、关闭焦点、Esc 与单一模态面板。截图视觉检查不等于读屏认证；真实手机软键盘、真实模型/QQ、Temporal 执行、持续压力未覆盖。BE-T1/T2/T3/X1/A2 边界及 UI-6/7/8 范围见实施报告。
