# 认证过渡实验室

独立 React/GSAP/Vite 实验，用于比较认证过渡与资源释放；不属于产品构建，也不接入真实认证。产品入口在 [App.tsx](../../web/src/App.tsx)，当前约束见 [Web 架构](../../docs/architecture/web-ui.md)。

在本目录安装独立依赖后执行：

```bash
npm ci
npm run dev
npm run build
npm test
```

dev 默认占用 5310；build 写本目录 dist；test 使用本目录 Playwright 配置。实验室与产品有独立实现和断言，CPU/Flip/资源释放等结论需在各自实际运行环境验证，不能相互代替。不要把实验控制或模拟认证带入正式构建。
