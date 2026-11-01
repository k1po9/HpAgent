# 第三方声明

HpAgent 以 [AGPL-3.0-or-later](../LICENSE) 授权。向网络用户提供服务时，须通过构建参数
`VITE_HPAGENT_SOURCE_URL` 提供对应源代码。

## rhine-lock

- 上游：<https://github.com/tmc5csxr9m-cpu/rhine-lock>，提交
  `a4df68c6bd9e01ecb7660659becf495957e57faa`
- 许可：AGPL-3.0-or-later
- 来源文件：`frontend/src/lib/scene/{Stage,shaders}.ts`、`frontend/src/lib/ui/`
  中的页面组件样式、`frontend/src/n8ao.d.ts`、`tools/assets/gen_textures.py`、`frontend/public/scene/{studio.hdr,smoke.jpg,hex.png}`
- 对应文件：`web/src/components/identity/`、`web/src/styles/identity.css`、`web/scripts/gen_identity_card.py`、`web/scripts/gen_identity_assets.py`、`web/public/identity/`
- 2026-11 的修改：修正 `rl_easeOutBack` 对负数调用 `pow`
  的未定义行为、曲线接缝处一片鳍片倾斜反向而突起的问题和卡面 UV；登录成功时卡片流汇入面板；窄屏流式布局、单帧静态渲染、停止与恢复、分级降质和资源失败上报；上游档案面板改用真实输入框和真实数据；标志和名称换为 HpAgent；移除调参 URL、待机、媒体与电源控件。降采样为 256×128。

上游说明其为《明日方舟》莱茵生命的同人作品，与 Hypergryph 无关联；相关名称和标志属于其权利人。HpAgent 界面不使用这些名称和标志。

## 字体

Montserrat 与 JetBrains Mono，SIL Open Font License 1.1，已子集化为 Basic Latin WOFF2，许可文本见
`web/public/identity/fonts/OFL-*.txt`。
