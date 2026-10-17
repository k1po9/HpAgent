> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# Mercury 兼容准备 · 未集成运动控制器

依据：当前分支实读 AppShell、Surface、InspectorHost、ConversationSidebar、ChatPane、HpThread、shell/conversationUi/workbench Store、EntryTransition 与 particles；用户附件 Mercury v1.0 的 SKILL.md、design-and-history、repo-adaptation、implementation-plan。Paper Flame 是色彩权威；业务范围与本轮禁止项以用户请求为准。

## 真实 DOM 边界

| 边界                                                 | 当前事实                                                                                                                          | 后续适配限制                                                                                                    |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| AppShell `.hp-main-canvas`                           | flex 阅读工作区，overflow:hidden；相邻 Context 和 Inspector；画布随 Inspector 与 sidebar fit 改变宽度                             | 可承载局部 overlay 主体；不能覆盖 Inspector、Canvas Header 或弹窗                                               |
| ChatPane → HpThread `.hp-thread__viewport`           | 已有 viewport React ref；滚动、MutationObserver、消息 anchor / offset 恢复；包含真实消息、ExecutionBlock、附件链接、Artifact 操作 | 候选阅读测量边界，不代表已批准收纳整个 viewport。其内既有交互状态需要独立于阅读表面；不要复制 DOM、截图或销毁它 |
| HpMessageView `.hp-text-part`                        | ReactMarkdown + GFM，真实可选中文本；同一 `.hp-msg__body` 还有 Run 与 Artifact                                                    | 文本可作为视觉采样边界，仍需先决定阅读表面是单条回复、独立阅读 Panel，还是历史区                                |
| `.hp-composer`                                       | 与 viewport 为兄弟，受控草稿、IME、附件、资源与发送/停止；线程容器也包含输入区                                                    | 永远保持业务可操作；禁止整个 HpThread 粒子化                                                                    |
| ConversationSidebar `.hp-conv[data-conversation-id]` | 真实会话 ID；筛选仅覆盖已加载项；历史分页；当前按钮 aria-current；可能在抽屉或屏幕外                                              | 仅用可见、当前且已测量的真实条目作为宿主；筛选/分页/滚动失去宿主时安全切终态                                    |
| Surface / InspectorHost                              | 1280 以下原生 modal，嵌套 Surface portal 到 body；模态 top layer 与层栈协调焦点                                                   | overlay pointer-events:none、aria-hidden；不能跨越 modal top layer；原有焦点和层栈契约保留                      |
| EntryTransition                                      | 登录/恢复 GSAP 单向过渡；固定粒子、预缓存 Path2D、resize/visibility/reduced-motion/卸载清理                                       | 已存在但不是 Mercury 可逆控制器。复用生命周期思想，不共享时间轴或重用登录目标集合                               |

响应式并非仅按 960px：AppShell 用 rail + sidebar + canvasMin + inspector + gap 判定 sidebar 是否容得下。1680px 四区可见；1280px Inspector 仍为区域但 Context 可能已隐藏。780px 为侧栏抽屉与模态 Inspector；390px 为底部 Rail 与全屏 Surface。未来 host 缺失以实际可见几何判断，不能仅用一个媒体查询假定有宿主。

## 光学兼容性

当前加入 `--hp-motion-star-neutral/hot/core/trail`、`--hp-motion-reflection/depth`。仅登录粒子读取 neutral/hot；其它 Token 为待使用的颜色钩子，没有 Mercury renderer/controller/API。

- 冷灰银 neutral #6E8290 对白底约 3.98:1，对 Canvas 约 3.78:1：装饰点可辨，低 alpha/细点仍需真实验证。
- 珊瑚 hot #EB6044 对白底约 3.34:1，只能少量点缀，不作为粒子主体色或普通小字。
- core #FFF3E9 与 reflection #C5D0D8 在白阅读面分别仅约 1.09 / 1.57:1：须依附冷灰轮廓、局部密度或局部暗衬。禁止为它们把整个 Main 变暗。
- trail 使用中性银 35% alpha，避免沿全路径铺暖光。它不是可读性保证；低 alpha 在浅底可能消失。
- depth #16212C 可提供局部深银材质，中段反射抑制与密度决定“重量”；左右清亮但不同，左端压缩/吸纳，右端真实几何发散。不能用降 opacity 代替发散。
- Paper Flame 的白色阅读面优先于装饰；银丝来自群体局部密度，不能添加独立大亮膜。光学、FPS、CPU、内存及整段星潮在真实 UI 上均未测，本轮没有此效果。

## 待实现的状态契约（文档提案）

当前业务没有 folded 状态。建议未来独立、会话 ID 关联、session-scoped 的展示协调器，使用 accountId + conversationId 防止跨账户残留；不写 API，不改 activeConversationId，不把 leaveConversation/navigate 当折叠。

```ts
interface FoldPresentationContract {
  conversationKey: string;
  target: "reading" | "folded";
  progress: number; // 0..1，唯一可寻址时间轴
  velocity: number; // 中途反向继承速度
  status: "idle" | "playing" | "settled" | "cancelled";
}
```

同一粒子 ID、seed、flow geometry；展开为同批物质的反向。DOM 视觉消隐与粒子出生共享 phaseAt。上述为后续提案，当前没有类型、Store、按钮或路由实现。

| 事件                                          | 后续必须满足                                                                   |
| --------------------------------------------- | ------------------------------------------------------------------------------ |
| fold/unfold                                   | 只改已批准阅读表面的展示；消息数/ID、URL、Run ID、草稿、附件不变；中途可反向   |
| 会话切换 / 页面导航                           | 终止旧动画并恢复 DOM，随后使用原有 route guard；不能绕过附件/权限/任务草稿确认 |
| Run streaming / 新消息 / 历史分页             | 不阻断 SSE、发送、停止、重试和 anchor 恢复；自动展开或通知方式待产品决定       |
| Inspector / modal 开关                        | 不覆盖其操作；宿主/画布几何变化时重测或安全收敛                                |
| reduced-motion / 后台 / resize / missing host | 立即到正确终态；不用动画锁住业务                                               |
| logout / session expiry / unmount             | 清理 RAF/observer/listener/mask/canvas；不能恢复旧账户 DOM                     |

## EXP-02 优先顺序与先决产品决定

1. **Conversation 条目**：当前选中、hover、键盘焦点和宿主确认的视觉语言；窄屏/筛选缺席时的反馈位置。
2. **阅读表面与消息**：先选收纳单位与真实 DOM 边界；长文、复制、链接、代码表格、滚动及 Run/Artifact 控件如何独立。不要默认整条 thread。
3. **Composer 发送/停止**：可逆的发送/运行/停止反馈与 IME、附件、禁用语义；输入区域始终独立。
4. **Run ExecutionBlock + Artifact 卡**：running/success/failed 的信息密度、微反馈、打开入口与与阅读区的空间关系。
5. **Inspector Header/Tabs/底部操作**：扩大阅读与返回、模态/区域切换的视觉反馈，维护 iframe 和草稿身份。
6. **Workspace 文件行 + Tasks 状态行**：选中/执行/注意/完成语言和焦点，使用相同色板做低幅度实验。

先明确 folded 时新消息/运行中如何呈现、是否保留每个会话的展示态、入口位置、无宿主退化、reduced-motion 终态与生命周期责任，再另行设计控制器与性能档。当前不推进 EXP-02。
