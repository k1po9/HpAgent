# Workspace v4.1 — P0～P5 总体验收

## 架构对应关系

Account 所有权与文件来源由 `stored_files` / `runs` 表达；长期位置为 `account_workspaces` / `workspace_nodes`；版本链为 `persistent_file_destinations` / `persistent_file_revisions`；持续授权和 Run 固定集合由 `resource_grants` / `run_resource_candidates` / `run_resource_access` 表达；发布、保存、版本提交分别由 `output_publish_operations`、`workspace_save_operations` / `research_run_save_intents`、`workspace_version_operations` 恢复。字节由 TenantFileStore 保存，RunFileWorkspace 按需复制至临时目录。检索和可见性为 `WorkspaceDiscovery`，GC 使用 `claim_file_deletion` 单一内核。

## I01～I19 证据矩阵

| 编号 | 结论 | 具体证据与缺口 |
|---|---|---|
| I01 | 通过 | P1 零复制双 entry 与跨账户拒绝；P5 1k/10k 同 file_id 只计 7 bytes。 |
| I02 | 通过 | P1/P3 移动改名与历史、hash 不变测试；Chromium P1/P3。 |
| I03 | 通过 | P0 独立 Research 输出 source Run 且 Conversation NULL；P4 30 次独立 Run。 |
| I04 | 通过 | P0 `test_published_docx_is_later_input_without_rewriting_origin`；purpose=output、direction=input。 |
| I05 | 通过 | P2 候选冻结、选前/选后版本和 500 上限；P3 旧 Run v1；P5 10k 显式拒绝。 |
| I06 | 部分通过 | candidate/fixed/materialized/read、published/saved 分表及时间可区分；缺少真实 10k 按需物化测量。 |
| I07 | 通过 | P2 撤销与并集、失败 Run 重试拒绝；P3 取消或无更新授权不得提交；P4 摘要撤销拒绝。 |
| I08 | 部分通过 | P2 数据库停止状态与阻塞 adapter 退出验证；真实 Temporal 文件工具 Activity 停止确认未跑。 |
| I09 | 通过 | Run scope 回收与 published/store 对象分离测试；P1 长期入口下载。 |
| I10 | 通过 | P0 独立 Task 报告输出、无助手消息下载；旧 Research API 测试已改为无授权不可跨聊天发现。 |
| I11 | 通过 | P4 Task 目录 ID、目录改名、运行中改目标仍旧目标、保存重试。 |
| I12 | 通过 | P3 历史版本 operation 重放；P4 已提交保存 operation 重放。 |
| I13 | 通过 | P3 并发 CAS 一个成功，失败输出 ready 可另存，旧 Run 修订仍可下载。 |
| I14 | 通过 | P1 GC/保存并发及失败重试；P3 修订、当前指针和固定对象保护。 |
| I15 | 通过 | P4 固定历史基线与撤销摘要拒绝；P5 摘要索引需 read_content、绑定 file_id/hash。 |
| I16 | 通过 | P4 required operation 未提交不能 completed；真实 Temporal 保存 Activity 故障重试。 |
| I17 | 通过 | P5 隔离空库 001～052 初始化；API/Worker schema gate、假迁移不匹配明确失败且不清库。 |
| I18 | 通过（静态） | 当前 `src/web_api`、`src/workspace`、`src/file_runtime` 与 Web 文件 API 无旧 logical_path 用户路由、旧 persistent-file 工具/审批卡、迁移开关或双读写；历史 SQL 031 作为空库迁移过程保留，最终 schema 已无 logical_path。未把其他领域的合法 fallback/Git 工具误删。 |
| I19 | 通过 | 普通 Workspace 文件、Run 输入、保存、版本、下载和 GC 路径不调用 Git；Git 工具只保留代码任务能力。 |

## 关键链路

1. **A PDF → 保存 → B 授权读取**：P2 API TestClient 上传实际 PDF 字节、保存 entry、授权 B、Run 固定候选；C 未授权 `file_id` 注入 403，撤销后停止。P0 PDF 重试物化字节测试。两项互补，尚无同一浏览器脚本贯穿 Agent 模型读取。
2. **Agent DOCX → 保存 → C 更新 → 旧 Run 旧版本**：P0 发布 DOCX 可作为后续 input；P3 跨 Conversation 更新、CAS 和旧 Run 版本测试使用文本 fixture。完整 DOCX 编辑业务链尚未以同一测试跑通，标为部分通过。
3. **独立 Research Task → 历史输入 → 发布 → 自动保存 → 下载**：P4 30 次受控依赖运行、固定授权历史、独立发布与保存；真实 Temporal Worker 保存重试；P0/P4 API 独立输出下载。外部 Research provider 未运行。
4. **移动/撤销 → 活动 Run 停止与提交拒绝**：P2 影响预览、取消状态与受控读取拒绝；P3 提交拒绝。真实 Temporal 文件工具停止确认未完成。
5. **GC 与保存/固定并发**：P1 GC/保存竞态与重试，P2/P3 固定和修订保留；未跑 10k 并发压力。
6. **发布/保存/版本故障恢复和历史幂等**：P0 发布后 DB 故障重试、P3 发布完成后保存服务重建与历史版本重放、P4 保存失败自动重试和已提交 operation 补状态；跨进程强杀的全窗口矩阵未完成。
7. **空库与启动**：隔离库 001～052 迁移、Chromium API 启动通过；schema gate 假记录探针明确失败且不删数据。

## 运行条件与环境重建

仅对 `hpagent_p5_20260926` 隔离库和 P5 Temporal namespace 执行初始化、探针及规模数据插入。现有开发数据库未重建。若维护者要重建开发库，应先停止旧 API/Worker 和处置旧 Workflow，然后明确执行 `dropdb --force` / `createdb`，运行 `PYTHONPATH=src .venv/bin/python -m persistence.migrate`，再启动新 API/Worker；普通服务启动只校验 schema，不自动迁移或清库。旧业务数据没有兼容路径。

**最终判断：代码基本完成，但真实 Temporal 文件工具撤销停止、跨进程强杀恢复的全故障窗口、完整 DOCX 编辑链以及 1k/10k 物化规模运行验收尚未完成。** 不进入 Optional P6。
