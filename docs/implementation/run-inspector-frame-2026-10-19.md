# Run Inspector：I. 总导航与外框

日期：2026-10-19，Asia/Shanghai。实施基线：`d011d65`。

本轮依据用户当前请求完成 I-1～I-5；计划文档中的其他窗口、并行安排和发布时机只作为背景。本次用户明确要求源码提交后同步 Beta，因此本单元检查通过后独立发布。概览、资料与输出、高级诊断页内结构保持；不代表 II、IV 已实施或获得视觉接受。

## 交付与视觉依据

| 项目         | 结果                                                                                                                                                                                                                                                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| I-1 扩大阅读 | Run 桌面移除两档按钮，左侧 12px 命中区域拖宽；悬停细线、col-resize、指针捕获。可聚焦 separator，左右方向键每次 16px，Shift 为 48px，Home/End 到边界。默认 460px、最小 360px、最大 880px，并为主工作区保留主题定义的 600px 阅读下限。1536px 屏幕实际最大 828px。宽度只保存在会话内，不改变对象 ID、查询生命周期或内部节点。 |
| I-2 关闭     | 40×40px 无边框、低强调 X 与既有 Esc；Surface 的最上层协调、焦点返回和退场流程保持。关闭留白面沿用刚才的 Run 宽度，避免拖宽后跳回旧档位。                                                                                                                                                                                   |
| I-3 标题     | 原接纳标题移入 Surface h2 左上；保留真实 receiver、TitleCapture 和双线装饰的到达机制。Run 装饰几何留白由 13/9px 放松到 18/12px，降低线宽和回声强度；其他对象装饰保持。修正旧版重复 h2 隐藏规则，键盘焦点改为贴合标题的银灰细线。                                                                                           |
| I-4 页签     | 概览／使用资料与输出／高级诊断的名称、次序、路由、键盘和 panel 关联保持。工具栏下直接进入页签；正文统一起点。                                                                                                                                                                                                              |
| I-5 同步执行 | 从正文移到标题右侧刷新图标，保留原同步范围：执行快照；高级页额外刷新 Trace 并失效模型输入缓存。独立资料、输出、审批仍由各区域刷新。悬停／聚焦说明范围，忙碌禁止重复点击，结果与错误有可读反馈；活跃执行继续复用原有实时投影逻辑。                                                                                          |

沿用深色主导航、冷瓷银灰阅读面，橙色用于当前页签和主动拖动，不增加成功色或新的品牌动画。Run 的工具栏约 76px，正文两侧桌面 22px／手机 14px。窄于 1280px 保持原有模态布局，不推广新拖宽交互。其他 Inspector 保留扩大阅读按钮。

## 验证证据

- TypeScript、修改文件 ESLint／Prettier、生产构建通过；已有大包体积提示未在本轮扩展处理。
- 9 个相关测试文件、102 项组件／store 测试通过，包含 Run、文件／HTML 成果详情及四类 Inspector 到达、快切、关闭与 Surface 层级。
- `playwright.run-frame.config.ts` 的 4 个 Chromium 用例通过：拖宽上下限和鼠标连续拖动；正文 DOM、输入 DOM 与草稿保留；三个页签和键盘；刷新成功／失败；1280、1279、960、768、390px；普通 Mercury 到达／减动；拖动期间转窄屏释放指针样式；嵌套 Surface Esc 仅关闭最上层并正确返回焦点。
- 浏览器使用真实 AppShell、InspectorHost、RunInspector 和 Surface，显式本地 fixture 数据，不连接 Beta 数据库。不能把截图视为真实模型、审批或 SSE 的生产验收。未使用 Beta 库跑迁移或测试。
- 768px 视口检查覆盖 1536px 工作区在 200% 下的等效可用宽度；未宣称完成操作系统／浏览器原生缩放或真实 IME、附件和长时 SSE 验收。
- 截图与录屏存放于源码目录 `artifacts/product-acceptance/run-frame-2026-10-19/`（忽略的生成证据）；before 来自实施基线的临时只读预览，after 来自本次真实组件。已逐张自审常规、最大、最小和手机外框。没有以旧 R6 接受记录代替本轮用户接受。

复跑：在 web 下运行 `node node_modules/playwright/cli.js test --config playwright.run-frame.config.ts`。测试只启动 Vite；API fixture 不访问后端。`e2e/run-frame-harness.*` 为浏览器测试入口，不进入生产 index bundle。

## 提交与 Beta 同步

唯一源码 `/home/hp/workspace/HpAgent`，Beta `/home/hp/deploy/hpagent-beta`。只提交本轮修改，保留启动前已有的设计索引和计划文件。源码提交后，以 fast-forward 同步 Beta；不覆盖其 `.env`。

已核实 project `hpagent-beta`，Compose 文件 `docker-compose.yaml` 和 `docker-compose.beta.yaml`，profile `web-prod`，受影响服务仅 `web-gateway`，现有镜像入口 `hpagent/web-gateway:offline`，绑定 `100.97.222.24:80`。API 使用容器健康检查验证，网关 `/` 不是 API ready。

前端制品独立输出，记录代码提交、静态包校验及运行镜像。使用已有 Nginx 运行镜像作为离线包装基底，覆盖经构建验证的静态资源；不拉取或清理镜像缓存。先保留原运行镜像的回退标签，再仅运行：

```bash
cd /home/hp/deploy/hpagent-beta
docker compose -p hpagent-beta -f docker-compose.yaml -f docker-compose.beta.yaml --profile web-prod up -d --no-deps --no-build web-gateway
```

发布前后比对 `.env` 摘要、其他服务容器 ID 和挂载；保留数据库与卷，不进行 down、prune、初始化或迁移。具体源码提交、镜像 ID、健康结果、云端入口结果和可执行回退命令写入同目录生成证据的 `release.md`，以实测结果为准。
