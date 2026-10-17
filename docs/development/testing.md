# 测试与验证

## 分层和环境

| 层 | 入口 | 范围与副作用 |
| --- | --- | --- |
| 文档/脚本隔离测试 | `make docs-check scripts-test` | 不访问业务库或模型；脚本测试使用临时文件和本机临时HTTP服务。 |
| Python静态/单元 | `make lint typecheck test-existing` | 先安装requirements-dev；`not postgres`不等于无基础设施，Work测试有自己的DSN要求。 |
| PG/API契约 | `make test-db test-api` | 真实角色、事务、权限、授权与跨账户；fixture可能TRUNCATE，必须专用可丢弃库。 |
| Temporal/replay/SIGKILL | CI的Phase D及`test/test_agent_segment_replay.py` | 受控namespace和实验Worker；不对正在工作的共享namespace执行kill harness。 |
| 前端静态/单元 | `make ci-web` | ESLint/Prettier、TypeScript、Vitest和构建；不是浏览器或真实Provider证明。 |
| 构建产物合同浏览器 | `make e2e-contract` | `playwright.entry.config.ts`，API/Auth路由夹具；不访问PG/Redis/模型。 |
| 正式API浏览器 | `make e2e` / `make e2e-canonical` | 真实API/PG/Redis/File Store，Fake执行器；会迁移、注册账户并写业务夹具。 |
| 发布/真实链路 | [脚本索引](../../scripts/README.md)中的release/model/benchmark入口 | 可能构建/启动服务、调用真实模型或对外请求，不作为安全日常测试运行。 |

## 后端环境

宿主安装 `requirements-dev.txt`，从仓库根执行。三个DSN应指向同一**专用测试库**的migration/API/worker角色；Redis、文件目录、Temporal namespace与端口也要隔离。

```bash
export MIGRATION_DATABASE_URL='postgresql://MIGRATION_ROLE:PASSWORD@localhost:5434/hpagent_test'
export APP_DATABASE_URL='postgresql://API_ROLE:PASSWORD@localhost:5434/hpagent_test'
export WORKER_DATABASE_URL='postgresql://WORKER_ROLE:PASSWORD@localhost:5434/hpagent_test'
export HPAGENT_MIGRATIONS_DIR="$PWD/persistence/migrations"
export REDIS_URL='redis://localhost:6379/15'
# 测试数据库及角色由操作员预先建立；不从业务库复制未经脱敏的数据。
make migrate
make test-db test-api
```

使用完整迁移链和原checksum；旧业务库门禁见[部署](../operations/deployment.md)。`make db-up`只启动Compose所配置的数据库，不能据此认为它就是专用测试库。`make test`包括基础设施集合，运行前必须检查fixture要求。

模型协议测试使用MockTransport，不证明真实Provider；历史5秒lease恢复不证明生产默认lease恢复速度。测试范围和排除项必须随结果记录。

## 前端与浏览器

```bash
make web-install
make web-lint web-typecheck web-test
make web-build
```

若已有服务使用`web/dist`，验证构建应写独立目录，避免影响现有预览：

```bash
cd web
npm run build -- --outDir /tmp/hpagent-verification-build
```

合同测试默认启动本机5311预览，需先构建。也可通过`HPAGENT_ENTRY_BASE_URL`复用操作员自己创建的独立静态服务（例如上述输出目录）；它不配置或启动API。`HPAGENT_BROWSER_EXECUTABLE`可覆盖浏览器路径，默认使用Playwright Chromium。

正式API套件：

```bash
# 延续上方显式隔离DSN与Redis；不可使用日常业务库。
export E2E_ISOLATED=1
export FILE_STORE_ROOT=/tmp/hpagent-dedicated-e2e-files
export WEB_API_PORT=8180
export WEB_DEV_PORT=5273
export WEB_PUBLIC_ORIGIN=http://localhost:5273
make e2e-canonical
# 或 make e2e：整个正式套件
```

`e2e-backend.sh`要求隔离标记与显式资源配置，启动自己的API并注册alice/bob；默认端口8180/5273可覆盖，Vite使用strictPort，Playwright不复用已有服务。套件串行执行，避免共享alice登录相互失效。`--list`只验证配置/导入和用例发现，不执行浏览器或后端，不能作为浏览器通过证明。

旧R416的29项用例和有效可访问名称修正已合入`web/e2e/`；`playwright.r416.config.ts`仅为canonical选择器，使用同一正式API环境，不再保留重复测试目录或依赖5334端口。阶段性的phase1/final/visual配置仍需其独立环境；不能直接纳入默认CI或向日常业务API执行。

截图、trace和JSON输出在忽略的`web/test-results/`，新实验输出在`artifacts/benchmark-runs/`。证据需形成独立受限批次，保留失败和环境错误；不要覆盖跟踪的历史摘要或将原始运行记录加入Git。

## 覆盖与结果解释

组件/store单测覆盖请求代际、授权、幂等、SSE、草稿/附件和Inspector身份；正式E2E覆盖真实页面与隔离API。合同浏览器还验证生产构建没有历史候选控制、查询参数不启用评审模式，以及减动/视口/认证恢复等行为。历史工具中未证明等价的断言继续保留，不能因当前套件通过就宣称全部替代。

当前UI约束见[Web架构](../architecture/web-ui.md)，历史接受和各次验证范围见[实施索引](../implementation/README.md)。真实供应商、生产Worker、QQ外发、真机IME/软键盘/读屏、其他浏览器和恢复演练分别验收；本次文档/脚本修改不声明这些路径通过。
