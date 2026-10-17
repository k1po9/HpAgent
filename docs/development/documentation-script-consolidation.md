# 文档与脚本收束变更摘要

日期：2026-10-17（Asia/Shanghai）。基线HEAD仍为`316e1184410fe4d3e6f902d0c80af10826b16a0f`；本轮开始的全部工作文件、Git状态及上一轮未提交patch已独立保全。没有commit、push、部署、业务数据操作、迁移历史变更或停止既有服务。

## 处置与维护位置

| 处置 | 实施结果 |
| --- | --- |
| KEEP | 现有运行时、领域/API、迁移和fixture；R1–R6决定/合同/冻结指纹/验证范围；82个尚未证明可完全替代的历史MJS及辅助工具 |
| MERGE | R416的29项用例与有效可访问名称修正进入`web/e2e/`；R1–R6现行约束进入现有`architecture/web-ui.md`；备份操作步骤只在operations/backup-restore维护 |
| MOVE | `scripts/queued-run-diagnostic.sql`与`schema-runtime-smoke.sh`进入`scripts/check/`，调用方/文档同步；新测试输出统一到忽略的`web/test-results/` |
| RETIRE | 11个R416重复源文件、23个仅输出路径/loopback端口不同的完整源码重复MJS、9份历史Prompt/模板；每项删除前校验当前SHA256与本轮归档一致 |

根README保留一条Compose开发路径，并核实Web注册、Origin与角色密码要求；docs导航按使用者/开发者/运维组织。当前约束在architecture，操作在operations，环境/测试在development，事实目录在reference；design/implementation标注历史，接受依据保持原身份。旧绝对工作树链接已改为经过存在性核对的相对链接。

## 稳定入口与脚本修改

完整参数、依赖、副作用和失败行为见[脚本索引](../../scripts/README.md)。Make/package提供`help`、`docs-check`、`scripts-test`、现有前端门禁及`e2e`、`e2e-contract`、`e2e-canonical`。数据库Make入口不再回退到日常业务DSN，缺少显式配置时在操作前失败。

- 发布smoke仅使用web-prod，显式执行后才可能build/up/迁移和发真实请求；检查迁移退出、全部必需服务、API/主Worker健康，以及登录303、Origin/CSRF、消息202、Run succeeded和本次Run来源的已完成助手消息。不输出响应/配置中的凭据。Document Worker无独立healthcheck时仍须人工验证poller。
- 备份必须显式指定仓库外绝对路径，解析符号链接后再次拒绝仓库内路径；受限独立目录、失败保留.incomplete。仍不包含Temporal/File Store一致性快照。
- 网关smoke的stub改为私有Docker网络中的独立容器，取消宿主8080冲突；仅清理本次创建的资源。schema检查脚本归位并修复cwd；Agent Benchmark包装器修复repo root与解释器回退。模型检查入口拒绝非法次数/类别/模式，保留原统计并在样本失败时非零退出。
- 历史MJS使用可配置浏览器和统一的轻量隔离/帮助保护，保留自身场景与断言。旧端口、离线HTML、路由和数据假设仍是专项环境限制，不能冒充当前CI入口。
- Benchmark代码/输入不动统计口径，新结果默认到忽略的`artifacts/benchmark-runs/`，不覆盖跟踪的历史汇总与逐条记录。

## 断言与验证

R416保留全部29项测试及断言，合入正式源文件，旧配置名只是canonical选择器。`r13-default-check`中的生产入口保护迁入合同浏览器测试：桌面/移动端均验证候选控制不存在、旧dataset不暴露、历史查询参数不能启用评审模式，并保留宽度检查。其余未证明覆盖等价的场景继续保留，不声明完全替代。

已执行：脚本隔离测试10项（支持的Python3.12环境）；Shell语法与Python静态解析；安全帮助检查；Make命令及显式DSN拒绝；CI路径和Compose静默配置检查；全量前端lint/typecheck、70文件603项单测、独立目录生产构建；合同浏览器9/9。构建保留既有>500kB chunk警告。当前及保留历史文档相对链接检查通过，远程URL/锚点未验证。

隔离canonical实际浏览器为26/29，失败断言没有删除或放宽：HTML成果刷新后重新打开、消息前插阅读锚点，以及UI8复合链路的登录阶段。起始快照原R416用例在另一套全新隔离环境聚焦复跑：前两项同样失败，确认是本轮前已有；UI8原用例与合并后用例分别在全新隔离环境聚焦通过，其完整套件失败仍保留为待查的顺序/环境敏感问题，不能据此宣布全套通过。正式套件使用本次专属PG/Redis/Fake API、独立端口与临时File Store，不连接现有业务数据；只移除本次创建的容器/网络。

没有执行真实发布smoke、备份/恢复、真实模型/QQ/供应商、生产拓扑、Temporal故障实验或网关Docker集成。测试发现不是浏览器通过；源码/单元门禁也不替代真实生产链。

## 归档、衔接与暂缓

补充归档编号`20261017-151850-consolidation`；原归档`20261017-123206`保持不变。`before.tar`保全本轮开始时1213个工作文件并已实际解包核对；manifest记录大小/SHA256/模式/时间；retirement-map列出每个退役项原路径、去向与替代；web-script-disposition逐项解释105个原始MJS的处置。按[归档索引](repository-archive-index.md)恢复到独立目录，禁止覆盖现有工作树。

最终逐路径增量清单/patch与起始状态保存在补充归档，用于区分上一轮1499个删除、37个修改及两个未跟踪文档，与本轮新增工作。已核对上一轮删除均保持删除，原审计报告及业务源码未改；没有全局源码格式化。

15346/15336的Windows评审脚本、5337预览与5173日常服务现场仍存在，未停止；相关保留目录和认证原型不在本轮退役范围。UI接受仍不替代真实模型、生产Worker、外发、恢复、真机与其他浏览器验收。疑似凭据生命周期处置仍由用户另行处理；清理与文档收束不消除Git历史副本。
