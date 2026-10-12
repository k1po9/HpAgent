# UI7 草稿用例的基线时序问题

未修改产品逻辑。

原始 main/a47d73b06a993d353b4177e282b69ddd28ffdb36 的 Vite 页面与迁入后的页面都复现：`seven viewports and boundary resizes keep Artifact iframe, draft and query count` 在首次 360px 断点检查时发现“修改指令”为空。原用例填入后立即 resize。

原基线 trace 中：登录填入始于 9166.808ms，修改指令 fill 始于 9541.344ms，首次 resize 始于 9549.428ms。填写时的目标快照仍包含：

```text
hp-entry data-entering="true" data-entry-source="login"
hp-entry-destination inert="" aria-hidden="true"
```

在用例中等待 `.hp-entry-origin` 卸载和 `.hp-entry-destination` 解除 inert 后，再填写草稿。原基线单项通过（3.6s），最终生产构建该用例通过（3.3s）。保留原 iframe 身份、草稿、查询次数、断点、字号与 reduced-motion 断言。

原 trace 私存于忽略的 `.data/phase1`，避免将测试 Cookie/CSRF 会话材料提交。只修改用例同步，没有修改 EntryTransition、Surface、业务 Store 或草稿处理。
