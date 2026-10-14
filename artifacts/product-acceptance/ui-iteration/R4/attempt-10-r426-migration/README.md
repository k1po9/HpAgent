# R4.2.6 React 迁回 · 待人审

按用户提供的 `HpAgent-R4.2.6-Living-Title-Capture-Standalone-Editable.html` 迁入当前真实 React；未增加设计方向。

- 冷瓷布局、导航与执行入口 SVG、真实状态图标、输入区样式逐项迁回。
- 银色渐变、4.6px 光头、单个尾迹和 64px 移动高光进入原 `filament.ts`；与真实 tip 共用 rAF 和 reveal mask。
- 标题统一显示“执行详情”；使用原到达/内容门控。标题框为 11px 横向、7px 纵向间距，起点与共享 `relayTargetPoint` 重合；关闭/切换卸载并取消局部动画，减少动态效果时静态绘制。
- 卡片停止按钮由 React 条件移除，输入区停止操作通过回归；Inspector 原有操作保留。状态查询、权限与路由继续使用现有代码。
- 离线 fetch 桥接、评审工具栏、夹具与 DOM MutationObserver 不进入产品代码。

## 验证

- TypeScript 与生产构建通过；已有大包提示仍存在。
- Vitest：63 个测试文件、519 项测试全部通过（`unit-tests.log`）。
- 本次改动的 TSX/TS 与验证脚本 ESLint 通过。
- 同数据浏览器对照：主要区域颜色、边框、字号、尺寸、阴影一致。两个透明背景的 `0%` 与构建器规范化后的 `0px` 序列化不同，不产生视觉差异（`optical-diff.json`）。
- 真实路径高光可见且位于原遮罩内，两个渐变使用 `userSpaceOnUse`；标题先于内容约 180ms，查询先于到达；标题框起点偏差小于 0.01px。
- 快速切换、关闭重开、减少动态效果、390px 小屏、对象不可用、输入区发送/停止均通过（`browser-check.json`）。
- 完整单文件通过 file:// 启动、标题框、关闭检查，外部请求 0、pageerror 0（`offline-check.json`）。
- 浏览器验证使用生产 React 构建与明确 API/SSE 夹具；本轮未验证真实后端登录与持久化。

## 评审与回退

- `reference.html` 是用户输入原稿的逐字节副本；原稿文件未修改。
- `BEFORE-AFTER.html` 为原稿/迁回版对照；`lab.html` 为当前生产构建加明确测试数据。
- `HpAgent-R4.2.6-React-Migrated-Standalone.html` 为完整离线迁回版，保留原始 SOURCE-REFERENCE、在 CHANGE-NOTES 追加迁移记录，并附新源码映射。
- `before-source/` 保存本轮修改前的真实文件；`migration-manifest.json` 记录本轮文件指纹。工作区原有未提交修改均保留。
- 本轮没有提交或部署；效果仍待用户评审。

本地预览： http://127.0.0.1:5327/BEFORE-AFTER.html
