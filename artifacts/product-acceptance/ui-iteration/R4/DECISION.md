# R4 整体验收决定

日期：2026-10-16（Asia/Shanghai）。

用户原话：“通过，下一步干什么”。该回复对应 attempt-16“四类组合、跨轮回归与真实隔离 API 验证”的“R4 整体待人审”交付，明确接受 R4 整轮。

## 接受与冻结范围

R4 的 Run / Artifact / File（含目录）/ Task 接力、实际光头抵达门控、标题先显、同对象 Tab/展开身份保持、版本与来源、父子返回、dirty guard、X 真关闭和纯装饰壳收束，以及与已冻结 R1–R3 的组合体验，按本轮交付范围接受并冻结。

已接受的16子页视觉继续冻结。File/目录与Task的局部接受决定分别保留在attempt-14、attempt-15；本决定完成整轮放行。

冻结源码：209份源码/配置，SHA-256汇总指纹 `3f157303d1526c5357cfb899fe445035e01bc3c0d78926dd4240a80d573b5758`。逐文件清单见 [ACCEPTED_SOURCE_FINGERPRINTS.json](ACCEPTED_SOURCE_FINGERPRINTS.json)，实际快照在 [attempt-16/source-snapshot](attempt-16-four-kind-closure/source-snapshot)。记录时已逐文件核实当前工作区与该快照一致。HEAD仍为9d47e55；冻结对象包括已接受的未提交成果，不能用纯HEAD替代或reset覆盖。

## 合同与证据

- [R4-ACCEPTANCE](../../../../docs/design/ui-iteration-2026-10/R4-ACCEPTANCE.md)及[逐项I01–I20核查](attempt-16-four-kind-closure/CONTRACT_CHECKLIST.md)。
- [验证结果](attempt-16-four-kind-closure/VALIDATION_RESULTS.md)、[时序与身份](attempt-16-four-kind-closure/TIMING_AND_IDENTITY.md)、[独立交接](attempt-16-four-kind-closure/HANDOFF.md)。
- 前端556项、隔离后端186项、29个原浏览器场景经分层复测通过；失败证据保留。
- 证据包历史清单中的925个非运行日志文件完整匹配；`logs/review-server.log`因评审服务访问持续追加，是运行日志，不参与静态冻结。原清单保留，没有改写历史证据。

旧attempt中的“待人审”“整轮未接受”等表述保留为交付时状态，以本决定为最新结论。

## 边界与下一轮

真实供应商、QQ、Temporal等not-run与环境限制仍如实保留在[REMAINING_WORK](attempt-16-four-kind-closure/REMAINING_WORK.md)；本次接受不把Fake、Worker事实模拟或未测项改写为真实验证通过。

下一轮是R5 Mercury阅读区可逆收纳。其产品语义仍需独立确认；本次用户询问下一步，没有授权自动启动R5实现。R5/R6未启动，未提交、推送或部署。
