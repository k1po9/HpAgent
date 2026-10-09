# 登录 / Cookie 恢复入口证据

截图来源于正式前端的构建产物（Playwright preview 5311），后端使用接口契约夹具。

- `recovery-wait.png`：身份验证未返回，工作台尚未挂载；居中品牌与未定进度圆环。
- `login-entry.png`：登录卡片保持同一 DOM，确认成功后进入转场。
- `recovery-particles.png`：恢复入口的粒子流（绘制颜色继承实际产品 tokens）。

7 项正式浏览器检查通过，涵盖粒子真实像素、Cookie/登录两入口、重试与过期、深链接、减少动态效果、动画期间失效与慢验证。测试命令与生命周期设计见 `docs/implementation/auth-entry-transition-2026-10-10.md`。

该记录不替代真实后端账号登录及低性能真机验收。
