# 综合回归测试库容量阻塞调查

受影响批次：[e2e-regression-capacity-blocked.log](e2e-regression-capacity-blocked.log)：62 项中 57 通过、5 项等待真实 HTML ready 超时。

5 个失败都发生在真实 API 创建 HTML 后等待 iframe。失败时页面中的 `complementary "HTML 成果"`、对象标题、版本 v1 和修改表单均正常；版本显示“等待生成”。这不是 Surface 隐藏或者错误挂载。

只读 SQL 结果见 [capacity-counts.txt](capacity-counts.txt)：专用旧测试库中 reminder queued=2、research_report queued=2，共 4 个同账户未结束 Work Run；5 个 Work 的 continuation.reason 为 waiting_capacity。对应 Artifact 版本均 queued，尚无 artifact_html Run 被调度。

代码依据：`src/resources/capacity.py` 的 admission 默认 `WORK_ACCOUNT_COORDINATORS=4`，计入 queued/running/cancelling Work Run；`src/run_domain/admission.py` 在容量满时写 waiting_capacity 并不创建执行；`src/web_api/fake_executor.py` 的测试执行器只完成 Chat / Artifact，其他 executor 的任务保持 queued。多次综合批次在同一个测试库创建提醒/Research，累计占满容量。最初 Artifact 及单独 UI-7 批次能通过，与这个随测试累积才出现的阻塞一致。

处理：保留旧隔离库 `hpagent_ui7_e2e_20261008` 及只读诊断，创建全新隔离库 `hpagent_ui7_final_20261008`、新文件目录 `/tmp/hpagent-ui7-final-files-20261008`，以默认容量运行完整最终 62 项。未清理旧库 Work、修改容量、跳过失败用例或调整产品后端。最终日志为 e2e-regression-verified.log。

重跑综合 E2E 时宜使用全新的专用数据库；测试执行器不覆盖真实 Reminder/Research/Temporal 的协调终态，不将本次 UI 自动化推导为这些执行器已验收。
