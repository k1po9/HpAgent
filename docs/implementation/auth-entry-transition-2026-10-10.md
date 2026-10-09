# 登录与 Cookie 恢复的统一入口转场

本次把独立粒子原型接入 `web` 的真实入口。开发服务器 5173 使用正式前端组件；5310 保留对比/故障模拟实验室。浏览器验收使用 5311 的同一前端和接口契约夹具，不创建账号、不写入业务数据库。

## 用户行为

- 手动登录或注册成功：保留原 Auth 卡片，原提交按钮显示“登录成功 / 账户已创建”与勾选标记，身份确认后以约 1280ms 粒子序列进入真实工作台。
- 重新打开或硬刷新：居中 HpAgent + SVG 细圆环。等待 `/api/v1/me` 验证 HttpOnly Cookie，成功后圆环补全，约 1000ms 粒子序列进入目标页面。
- 圆环表示未定进度；不输出虚构百分比。无 Cookie 的首次访问也需验证，确认未登录后显示表单，不播成功动画。
- 请求超过 4 秒显示耗时提示；会话探测超过 12 秒中止并进入可重试错误界面。401 回到登录。
- 后台身份检查、工作台页面切换、对话切换不重新播放入口转场。
- 系统减少动态效果：静态圆环，成功后约 160ms 淡化，不挂载粒子 Canvas。

## 组件与生命周期

`App` 持有入口覆盖层和目标工作台两个稳定插槽；登录验证阶段不会提前卸载表单。`auth` store 的 `entrySource` 标记登录或恢复，`assemblyPending` 只在建立新身份边界时开启，动画完成后清除。旧圆点 `Assembly` 及样式已移除，原用例迁移为入口状态和浏览器回归。

身份验证前不挂载业务内容。验证成功后 `AppShell` 提前挂载，在入口层后恢复路由和加载数据。等待两次动画帧后读取实际布局，GSAP Timeline 编排导航、侧栏、主画布、首屏内容与 Composer；完成时仅卸载入口层，不重建工作台。聊天数据较慢或失败时保留工作台自身的加载/错误恢复机制，入口不等待无限的数据请求。

`EntryPendingContext` 暂停 `Surface` 的模态激活，防止原生顶层 dialog 或 Portal 在转场期间盖住入口；子树保持挂载。完成后恢复模态状态，若没有激活的模态，则把焦点交给当前页面标题。过渡期间目标内容使用 `inert` 与 `aria-hidden`；独立 live region 宣告成功。

`EntryTransition` 使用 `@gsap/react` 的 contextSafe 管理延迟启动的 Timeline。完成、失效、卸载、窗口改变、进入后台及运行时减少动态效果均可收尾；清理 RAF、监听器、Timeline、临时样式和 Canvas 路径/像素缓存。另有 2200ms 看门狗，避免异常帧调度造成不可操作的入口。会话失效优先于任何视觉完成回调。

## 绘制预算

粒子中性色与极少量 Accent 从正式页面的 CSS tokens 读取，保持圆环与工作台颜色连续。正式版本沿用优化后的 Canvas 2D 方法：标准 168 粒子 / 像素密度上限 1；启发式低功耗 48 粒子 / 上限 0.75。预计算 81 组路径，每帧最多 8 次批量填充，只擦除前帧实际边界。卡片采用矩形边缘采样，恢复入口采用圆周采样；目标来自实际工作台布局，隐藏侧栏和不可见 Composer 不参与流向。

登录和恢复的主视觉均为粒子，不将 Flip 加入正式入口；独立实验室保留 Flip 对照。低性能真机流畅度仍需现场验收，原型/软件 CPU 降速不能代表所有移动设备。

## 验证命令

```bash
cd web
npm run build
npm test
npx playwright test --config playwright.entry.config.ts
```

独立原型：

```bash
cd prototypes/auth-transition
npm test
npm run dev
```

原型新增 Cookie 恢复选项，支持快速成功、慢请求、Cookie 过期、网络失败及重试。正式前端的浏览器测试覆盖实际 Canvas 像素、登录卡片身份保持、HttpOnly Cookie 夹具/刷新、错误与过期、静态圆环降级、移动任务深链、视口改变、动画期间会话失效、慢请求与运行时动态效果切换。单元测试补充超时、迟到请求、后台检查不重播及模态保持挂载。

## 本次验证结果

- 独立原型：12 项浏览器测试通过。
- 正式前端：构建通过；7 项基于构建产物与接口契约夹具的浏览器测试通过。
- 全量单元测试首次为 411/412；唯一失败为既有文件夹图标从 emoji 改为 SVG 后，旧用例仍查找 `📁 项目`。仅将断言更新为实际可访问名称 `项目`，该测试文件复测 3/3 通过，其余已通过用例未重复跑全量。
- 最后的品牌色与按钮成功态调整后，重新构建并分别复测粒子恢复、登录及 Cookie 刷新链路，通过。
- 修改涉及的代码通过 ESLint / Prettier 检查。
- 截图保存在 `artifacts/product-acceptance/auth-entry-2026-10-10/`。浏览器测试使用模拟 API 契约和 HttpOnly Cookie 夹具，不等于已验证真实后端的完整登录链路；实际登录 API 与 Cookie 机制保持原有实现。

## Docker 开发入口依赖同步修复

5173 实际由 `web-dev` 容器提供，使用独立的 `web-node-modules` 卷。仅在宿主机安装 GSAP 不会更新该卷；原启动命令只检查 Vite 是否存在，因此遗漏新增依赖，导致 `Failed to resolve import "gsap"`。

已通过 lockfile 在容器执行 `npm ci`，并仅重建前端容器。启动命令现在比较 `package.json` 和 `package-lock.json` 的 SHA-256 指纹，变化时先同步依赖，失败时不继续启动 Vite。以后修改依赖文件后执行 `docker compose restart web-dev`；纯源码变更仍由 HMR 更新。

验证运行中的容器入口可使用：

```bash
cd web
HPAGENT_ENTRY_BASE_URL=http://127.0.0.1:5173 npx playwright test --config playwright.entry.config.ts -g 'valid cookie|login keeps'
```

设置该地址时测试复用已有服务，不另起宿主机预览服务器，也不会停掉已有服务。
