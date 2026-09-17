# 仓库资产普查

基线：`4ab07fbddf33e9a0dc652833acd9edd519e21fea`，分支 `feat/hpagent-web`，初始工作区干净。扫描 tracked 文件及非忽略新增资产；审计目录不作为被审计资产。共 1078 个文件。

| 资产类 | 文件数 |
| --- | --- |
| historical_evidence | 442 |
| backend_source | 239 |
| test | 144 |
| docs | 54 |
| historical_docs | 52 |
| migration_schema | 33 |
| frontend_source | 31 |
| deployment_configuration | 24 |
| frontend_build | 15 |
| benchmark | 12 |
| operations_development | 11 |
| smoke_probe | 7 |
| development_reset | 5 |
| development_asset | 3 |
| migration_admin | 3 |
| compatibility_inspection | 2 |
| backup_restore | 1 |


`repository_inventory.csv` 是全仓文件清单。`architecture_truth_table.csv` 覆盖 530 个非历史文档/证据资产，并为热点追加符号行。后端 Python 239 个、前端 TS/TSX 44 个全部有独立文件行；这包括测试和 .d.ts，不等于生产模块数量。web/src/styles.css 也已纳入。

数据普查包含根 persistence 下的 SQL（含 trigger/function）、config 下所有现有配置、所有 src package、scripts、tools、web 构建/网关/测试配置。历史 docs 和 benchmarks 只保留资产角色/证据，不展开为生产调用图。未读取私有 .env/.data 内容，没有启动业务进程。

## 本次判断口径

Python 用标准库 AST 解析 376 个文件，语法错误为零；Frontend 用仓库现有 TypeScript compiler API 解析 import/export、type-only 和实际值引用。扫描不 import 业务模块，因此不会连接数据库/模型/QQ。`dependency_edges.csv` 的 fan-out 包含外部库与标准库，fan-in 是引用源文件去重计数，不是请求流量或性能指标。

`runtime_edges.csv` 区分静态调用位置、构造、DI 绑定、Temporal 注册/调用、HTTP 路由、tool callback、Outbox 和前端值引用。它是代码层面的“可能调用”证据图，不是线上调用跟踪；条件保存在 config_gates。http_routes.csv 同时含42条生产路由和2条 test/web_api 中的测试路由，按 handler 路径区分。`reachability_proofs.csv` 只沿这些边找 root→符号链，不沿 import 图推断生产使用。`static_root_candidates` 特意单列，不能拿来替代 runtime_roots。

`UNKNOWN` 包含包重导出、纯协议/类型、未解析 receiver、历史数据消费者等，含义是“当前证据尚未闭合”，不是“死代码”。包级混合分类使用 UNKNOWN 并枚举子文件分类；符号行也不会自动继承文件生产标签。源码 docstring 在 responsibility 中是线索，实际边/SQL/构造证据用于约束其含义；明确失实之处见 drift 表。

## 运维 / 测试

`ops_entrypoints.csv` 列出 35 个 Python/Shell 人工入口（含 tools、web/scripts），细分 migration/admin、backup、reset、benchmark、smoke 和历史检查。OPS_ADMIN 表示人类运维/开发探针入口，不承诺脚本是生产发布支持接口，也没有运行它们。

测试文件共 144 个。test_refs 是直接模块引用证据，不能证明对应业务行为已被断言，也不能作为生产入口。重点测试族包括 test/test_agent、durable contract/hardening、worker recovery、document integration、web_persistence、web_api、file approval、frontend unit/e2e。源码 AST 与测试 import 图已覆盖，未运行真实模型/QQ/Research E2E。

## 已存在的本地工具检查

- `npm run typecheck`：通过。
- `npm run lint`：ESLint 和 Prettier check 通过。
- `docker compose config --quiet`：通过；没有输出解析后的敏感环境变量。
- 全 Python AST parse：通过，比 compileall 更适合本轮只读审计，不生成源码目录 pycache。

未安装/升级任何依赖。一次 `python` 命令因 PATH 中无该别名退出127，随后用 `python3` 完成扫描；这不是项目测试失败。
