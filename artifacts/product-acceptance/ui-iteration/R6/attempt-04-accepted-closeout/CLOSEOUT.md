# R6 正式收尾

用户2026-10-17明确接受R6并授权清理、迁入和commit。本次基线241/241与最终attempt-03一致，源码指纹bcc85cb4b9f68570d33a217709636e937de1594d65910cf559ca5223a0efe867。实际成果本就在web/src，收尾将已接受的File/Task、R5、R6依赖与回归测试正式纳入Git；没有从演示HTML覆盖源码，也没有迁入后续离线创意稿。

## 本次新测

- Vitest：70文件603测试通过（logs/vitest.log）。
- tsc-b：通过；production vite build通过，仍有既有>500kB chunk警告。
- 57份改动TS/TSX/CSS范围ESLint/Prettier通过（CSS只做Prettier）；R416专用E2E源码ESLint通过。
- 冻结清单与工作区/暂存区逐文件核对，防止提交遗漏未跟踪Mercury或File/Task回归文件。
- 专用Playwright用例发现通过（--list：9文件29项），只验证配置/导入，无浏览器执行；摘要见evidence/validation-summary.json，逐文件暂存核对见evidence/index-parity.json。原始日志含工具自身空白，保持字节不变留在本地logs；Git纳入摘要及其哈希。
- 原135浏览器场景、24切换、真实API及资源循环为对应attempt的历史本轮证据；本次收尾不冒充重新跑过浏览器/后端/供应商。

## 清理

- 27个未提交的阶段性评审MJS从web/scripts移至archived-review-scripts，逐文件hash保持。映射见ARCHIVED_REVIEW_SCRIPTS.json。它们是历史评审工具，不是生产模块；配置/专用r416-e2e源用例仍在web。
- 只移除了根目录node_modules中的生成Vite测试缓存（原目录只有.vite/.vite-temp）。备份与文件清单保留于本attempt；web/node_modules依赖未动。
- 历史HTML、screenshots、source-snapshot、before-source、ZIP、测试库/文件备份、离线创作包以及本地Skill目录均保留。Git忽略这些本地档案，正式接受决定/合同/小型指纹/归档工具显式提交；不会将约GB级重复产物、测试数据库或供应商配置混进commit。
- R6旧Linux临时静态服务5346/5348收尾时已不监听，不需要杀进程。现有私有preview5337及Windows15346/15336是有效评审入口，保留；未停容器、未清数据库/文件库、未改日常服务或Windows保留端口。

## 正常产品入口

生产代码为web/src，由web/index.html正常构建；所有模拟桥只在本地artifacts评审包。正常部署不需要打开评审HTML或启用任何离线场景。使用项目既有部署/环境文档配置真实API；本次没有生产接入或部署。

本地评审仍为http://127.0.0.1:15346/REVIEW.html；实际隔离API+Fake为http://127.0.0.1:15336/（alice/bob / e2e-password）。Windows15346根指向attempt-03，Windows保留端口5286–5385导致旧5346/5336不能使用。恢复方法在attempt-02 README和attempt-03 HANDOFF；启动前重新核实容器/端口归属，不能盲用旧IP或产品库。

## 历史脚本恢复

需要复跑旧R4脚本时，先核实隔离环境及旧脚本端口/账户假设，再把需要的单个文件从本目录archived-review-scripts复制回web/scripts。使用不覆盖现存文件的复制方式，例如cp -n；执行完可移回本地临时目录。归档保持原始源码，可能依赖原web目录的Node模块解析，不能直接从归档路径运行并声称同一环境。当前R6脚本继续位于原attempt的scripts，不受此整理影响。

## 接受与后续

R6/DECISION.md及R6-ACCEPTED-CONTRACT.md为最新权威；旧attempt文字保留历史。离线Motion创作包原样保留，后续返回新稿须独立审查后仅迁入获准效果模块。没有R7或新业务扩展，没有推送/部署。本次正式提交信息通过git log及本地evidence/commit-result.json查询。
