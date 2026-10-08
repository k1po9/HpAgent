# UI-1～UI-8 保留验收证据

当前交付、缺陷关闭和放行边界以[重构交接](../../../docs/implementation/ui-refactor.md)为准。此目录保存必要的原始结果，2026-10-09 合并整理；不同批次不累加，未完成的人工/外部验收仍保持未验证。

## 初始综合验收

`initial/logs/` 为 2026-10-08 的首次综合结果：前端 408、Chromium 76、生产 dist 2 通过；后端 197 项中 182 通过、15 失败。

- [环境与设计来源](initial/logs/environment.json)、[后端 JUnit](initial/logs/backend-results.xml)、[失败清单](initial/logs/backend-failures.json)。
- [前端退出记录](initial/logs/frontend-exits.json)、[Chromium](initial/logs/browser-results.json)、[生产 dist](initial/logs/production-results.json)。
- [A→B→A 修复前反例](initial/logs/send-race-before.log)、[修复后定向回归](initial/logs/send-race-after-final.log)、[脱敏网络顺序](initial/logs/ui2-concurrency-network.json)。
- [构建体积](initial/logs/build-assets.json)、[生产性能样本](initial/logs/production-performance.json)。

## 完整修复批次

`fixes/` 为 2026-10-08 最后完整修复结果：前端 408、Chromium 76、生产 dist 2、后端 206 通过。生产 dist 和定向测试与完整集合有重叠。

- [最终审计](fixes/logs/final-audit.json)、[隔离环境](fixes/logs/final-environment.json)、[工作区指纹](fixes/logs/final-workspace.json)。
- [后端 JUnit](fixes/logs/backend-results.xml)、[命令与退出码](fixes/logs/backend-exits.json)；[前端退出码](fixes/logs/frontend-exits.json)、[Vitest 日志](fixes/logs/unit.log)。
- [完整 Chromium](fixes/logs/browser-results.json)、[生产 dist](fixes/logs/production-results.json)。执行时源码指纹在 `*-command.json`，当前文档整理不改写这些历史记录。
- [原失败复现](fixes/logs/baseline-targeted.xml)、[SSE 握手及取消竞争反例](fixes/logs/new-regressions-before.xml)、[Artifact 取消前](fixes/logs/artifact-cancel-before.xml)/[后](fixes/logs/artifact-cancel-final.xml)。
- [Canonical 策略与真实状态链](fixes/logs/canonical-strict.xml)、[完整定向分组](fixes/logs/f8-01-02-final.xml)、[API import / sandbox](fixes/logs/import-sandbox.log)、[058→059 升级](fixes/logs/migration-upgrade.json)。
- [最后完整截图](fixes/regression-evidence/)含 UI-2～UI-7 的 173 个输出；[原路径映射](fixes/logs/regression-evidence-map.json)保留运行时路径，清理后的实际位置在 `fixes/regression-evidence/`。
- [浏览器资源观测](fixes/browser/)、[生产 dist 资源观测](fixes/production/)；[响应式量测失败 Trace](fixes/responsive-failure/)、[断点语义同步复验](fixes/logs/responsive-sync-results.json)。
- [当时 provider 无 HTTP 响应](fixes/logs/provider-roundtrip.json)为历史故障，当前访问结果见下一批次。

## 当前模型与范围复验

`recheck/` 为 2026-10-09 最终代码的非 QQ/手机范围复验。

三层测试库为 `hpagent_ui8_recheck_{backend,browser,production}_20261008_04`，Temporal namespace 为 `hpagent-ui8-recheck-20261008-04`；后端/浏览器/生产 Redis DB 分别为 11/13/12，浏览器端口 8196/5289，生产 dist 端口 8198/5291。均为独立测试环境，不是业务库；新复验须另建环境。

- [ModelClient 多轮请求摘要](recheck/logs/provider-roundtrip.json)：两次合成 echo 工具调用，第三次返回 OK；仅证明 Worker 网络中的真实供应商边界，不等于业务 Run Activity 通过。
- [后端 JUnit](recheck/logs/backend-results.xml)、[命令与筛选](recheck/logs/backend-exits.json)：200 通过、7 QQ/NapCat 用例按范围排除，包含未来时间戳回归。
- [前端门禁](recheck/logs/frontend-exits.json)、[Vitest](recheck/logs/unit.log)：54 文件、408 项。
- [桌面 Chromium](recheck/logs/browser-results.json)/[命令](recheck/logs/browser-exits.json)：13 通过；[生产 dist](recheck/logs/production-results.json)：1 通过，与桌面任务场景重叠。
- [桌面任务资源观测](recheck/browser-evidence/)。测试库/namespace/端口等条件见[交接](../../../docs/implementation/ui-refactor.md)与上述原命令；复验必须重新配置独立环境。

## 早期反例与本次整理

`history/ui-3`～`ui-7` 保留已经转为正式回归的关键修复前日志；实现、测试与早期计划可从交接列出的 Git 提交恢复。重复截图、过程目录、临时测试源码副本和本机专用编排脚本已删除。复跑使用[测试指南](../../../docs/development/testing.md)，输出新的批次，保留目录不作为默认输出。

[retained-evidence.json](retained-evidence.json)记录合并时每个文件的原路径、新路径与 SHA-256。历史 audit / manifest 的旧路径、文件数量和“原件已恢复”描述仅说明当时执行条件，不能要求已清理目录继续存在；原始内容保持字节一致。`docs-sync/` 保存本次实际运行的前端检查及文档/证据校验，不与历史批次累加。
