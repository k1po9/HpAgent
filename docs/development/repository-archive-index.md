# 仓库外归档索引

归档编号：`20261017-123206`（2026-10-17，Asia/Shanghai）。基线：`316e1184410fe4d3e6f902d0c80af10826b16a0f`，分支 `main`。本次不创建提交、不推送、不部署。原有未跟踪清理审计报告保持原样。

完整归档由本机管理员保管，位于仓库之外，访问权限仅限持有人；不在可提交文档列出私密内容或服务配置。交付消息提供绝对位置及清单校验值，`manifest.json` SHA256：`88f19a012aab65df871627829125d5e5bcac9dba84c9c444fadd9c6083449fb0`；`repository.bundle` SHA256：`fb66d95116fb71626f8fb5450a793bc7303a0e41e48615191457375a09d4f209`。归档根 `SHA256SUMS` 校验各归档文件，`manifest.json` 逐文件记录原路径、类型、大小、SHA256、符号链接、元数据和归档去向。

| 原路径 | 归档文件 | 仓库保留 |
| --- | --- | --- |
| `artifacts/` 历史产物 | `evidence.tar`；其中设计输入在 `design-inputs.tar` | 接受决定/合同/指纹、验证与反例文字、Benchmark 报告/汇总/逐条数据 |
| `ui_skill_delete_after_done/` 及历史设计输入/离线创作包 | `design-inputs.tar` | 当前合同继续由现有设计及架构文档承担 |
| `dist/` | `release.tar` | 离线导出/加载脚本保留，可生成新制品 |
| 工作区源码、认证原型、原有未提交内容 | `working-tree.tar`，另有 Git patches | 原有修改不覆盖；原型暂缓退役 |
| 已提交源码/refs | `repository.bundle` | Git 历史不改写 |

归档保留原目录层级、隐藏文件、模式/时间戳/uid/gid 与符号链接目标；它不是业务数据库、File Store、依赖或运行中服务的一致性备份。明确排除 `.env`、`.data/`、卷、`tools/vectors/`、`web/node_modules/` 和范围外忽略运行数据。

## 恢复

先在受限目录按 `RESTORE.md` 校验 `SHA256SUMS`，解包所需 tar，按 `manifest.json` 核对大小和 SHA256，再把明确需要的原路径复制到独立工作树；禁止直接覆盖当前仓库。Git 可从 bundle 克隆基线；未提交/忽略材料必须从 tar 恢复。旧评审脚本恢复后先核实 Node 解析、端口、隔离 API 与数据边界，不能直接在生产执行。

## 当前保留与限制

R1–R6 的最终接受决定、合同、R6 收尾、冻结清单、验证范围和未完成验收继续保留；最新权威为 [R6 合同](../design/ui-iteration-2026-10/R6-ACCEPTED-CONTRACT.md)。历史文档里的命令、内联路径及旧统计描述当时环境；已归档链接经此索引恢复，不是当前运行前置条件。

Windows 15346/15336 仍使用 R6 attempt-02 的服务脚本与 attempt-03 静态根，因此这两个目录完整暂缓删除；5337 使用 `web/dist`，亦保留。WSL interop 仍持有 R6 `offline-motion-export/attempt-01` 的独立 HTML 文件句柄，因此该离线创作包也完整保留（已从归档补回缺失同包文件，未覆盖现存文件）。后端缓存位于运行中源码挂载，暂缓清理。认证原型仍有 Flip 对照、CPU 测量、重复粒子释放等未确认完整覆盖的断言，保留实现和测试。一旦完成覆盖或服务退役，需重新核验并另批清理。

运行中材料可能在归档后追加；删除前逐文件重核，变化项暂缓。疑似凭据日志移入受限归档，若为真实密钥仍须撤销/轮换；当前树清理不能消除 Git 历史副本。


## 文档与脚本收束补充（2026-10-17）

补充归档编号`20261017-151850-consolidation`，与上方原归档独立保存，不修改原校验表。开始时工作文件见`before.tar`和manifest，实际解包/大小/SHA256核验通过；逐路径退役及替代见`retirement-map.json`，105个阶段MJS逐项处置见`web-script-disposition.json`。起始Git状态、上一轮patch、本轮增量清单/patch及验证结果分别保存。

退役文件按原路径在before.tar中恢复到新的受限目录，先校验后逐项使用；不覆盖当前工作树，不直接执行旧环境脚本。历史Prompt和R416副本已退出当前入口，接受决定/合同/验证边界保持原路径；当前维护位置与残余失败见[本次变更摘要](documentation-script-consolidation.md)。原段落中的服务状态是当次观测，本次亦已重新核实Windows15346/15336与Linux5337/5173，不保证未来继续有效。

补充 `before.tar` SHA256：`3419e966b035f259244b43b418f295bedc0264da7e9af8d2d2873bec3fb45a57`。
