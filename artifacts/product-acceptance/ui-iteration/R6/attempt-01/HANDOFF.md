# R6整体待人审

2026-10-17（Asia/Shanghai）。R1–R5保持已接受；R6实施、自审、修复及验证交付，未自行接受。未提交、推送或部署。

可操作入口：

- http://127.0.0.1:5346/REVIEW.html — 当前production React完整评审包，确定性浏览器夹具；不连接API/DB/供应商。
- http://127.0.0.1:5346/review/after/index.html — 纯当前React夹具；加?r6Auth=1展示登录失败/过期/换账户模拟；?r6=empty/slow/error/denied/long/search-error展示边界。
- http://127.0.0.1:5336/ — 当前Vite production preview →再次核实的hpagent-r416-final真实API；alice或bob / e2e-password。FakeRun/FakeArtifact启用，网络internal，无真实供应商或外发。
- offline/REVIEW.Standalone.html — 可直接file://打开，当前React、合同/矩阵/自审/验证/交接、代表截图、源码/备份/完整证据ZIP。评审工具和夹具均在artifacts，没有嵌入产品。

建议操作：左侧单击切换并核对独立草稿→收纳/展开→Run/Artifact/File/Task深层与返回/X→空间目录/搜索/长名称→新目录草稿切任务取消与放弃→上传选择/改名/另存的Escape两层保护→任务创建/修订/授权→返回AI继续发送→退出换账户。真实API写入只在一次性测试环境内，未提交选择关闭有确认；已经提交的上传/保存仍可从原记录继续。

源码基线240/240匹配R5接受指纹d767ac3b8d22330f20dbe63eb930f8020b0da56b36e8279c929f5dcf03996d22；HEAD仍9d47e55。before-source和diffs保留所有已接受未提交成果；不能reset/clean/用HEAD覆盖。最终241份候选快照指纹3a79275d06c183029952a4380b67f569fb679c4602b5d4eb496f42e8b42b2198，详见SOURCE_FINGERPRINTS.json/SOURCE_FINGERPRINT.txt，精确R6增量为implementation.patch。14份修改/新增，R5/Mercury源码保持，样式仅Workspace表格单元格顶部对齐；16子页不重设计。

真实测试细节、当前数目、资源实测及未测项见VALIDATION_RESULTS、ACCEPTANCE_MATRIX、SELF_REVIEW、REMAINING_WORK。历史证据保留，没有计入本轮结果。

## 启动

在仓库根，先ss -ltnp核实端口归属；已有本attempt服务则复用，不覆盖其他进程。纯静态评审仅绑定127.0.0.1：

```bash
cd /home/hp/workspace/HpAgent
python3 artifacts/product-acceptance/ui-iteration/R6/attempt-01/serve-review.py --port 5346
```

真实preview先验证：

```bash
python3 artifacts/product-acceptance/ui-iteration/R6/attempt-01/scripts/check-isolation.py --allow-missing-preview
python3 artifacts/product-acceptance/ui-iteration/R6/attempt-01/scripts/start-preview.py
```

脚本核对原容器ID、internal网络、专用hpagent_r416_final、HPAGENT_ENV=test、Fake、无供应商密钥、只读repo、受限角色和59迁移checksum，再根据当前IP启5336。不固定信任旧容器IP，不省略HPAGENT_API_TARGET而落到默认8000。

本轮遇到原专用容器停止和tmpfs测试库/文件丢失，已核对原身份后仅恢复缺失数据。重启仅用scripts/restart-isolation.py；数据库确实不存在时才可用restore-missing-test-data.py（来源R5.0指纹备份，拒绝覆盖现存DB/文件）。本轮最新只读备份另存recovery/，若将来要重建到新空专用库，应先核对其指纹与目标；不要对任何现存库pg_restore --clean，不触及日常产品容器。详细来源见storage-and-runtime、isolation-before-reuse、restored-missing-test-data。

## 复验与恢复

Node固定Linux /home/hp/.local/share/hpagent-review/node，PATH加其父目录；避免Windows npm。在web运行./node_modules/.bin/vitest run、tsc -b、vite build；仓库根运行scripts/static-check.py及浏览器脚本。复测先复制JSON/日志到新attempt或新文件名，避免改写此证据。scripts/package.py只打包当前dist，不负责构建；重建后先finalize-source.py核对快照再打包，不可沿用旧hash。

生产回退仅手工核对implementation.patch涉及文件与before-source，先保存后续修改；见BASELINE_RESTORE。不要用整份Git diff撤销此前File/Task/R5成果。没有后端回退步骤。

最终停“R6整体待人审”。若反馈返修，保留本attempt，另建attempt；只有用户明确接受后才能冻结。不得自动启动R7、生产接入、提交、推送或部署。
