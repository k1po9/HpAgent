这是 HpAgent「会话切换、阅读区收纳/展开」离线 React 创作包导出窗口。

请完成导出、实际打开验证和交付，不只给方案。我会把生成的单文件交给另一个创意窗口修改效果，最后把新文件带回原工程窗口审查迁回。

本窗口只负责忠实导出当前基线和约束，不提前替我设计新效果，不修改产品源码，不连接实际API/数据库/供应商，不提交、推送或部署。

一、基线与必要阅读

工作目录：/home/hp/workspace/HpAgent。
先检查祖先/仓库AGENTS.md、git状态及当前源码，不reset/clean，不覆盖未提交成果。

最新动效基线：
artifacts/product-acceptance/ui-iteration/R6/attempt-03-unified-mercury-timing/
先读其中HANDOFF.md、INTERACTION_CONTRACT.md、SOURCE_FINGERPRINTS.json，核实当前相关文件指纹。
候选汇总指纹：bcc85cb4b9f68570d33a217709636e937de1594d65910cf559ca5223a0efe867。
HEAD不能替代实际工作区。若源码已有后续变化，先保存并说明，以核实后的最新实际实现为准，不盲目恢复旧快照。

当前会话切换与手动收纳/展开已经共用createMercuryTimeline：omega=8.5×默认倍率0.35；progress≥0.98的两个宿主端点区间hostTempo=2。不要导出此前切换使用23系数的旧版。实际两方向约2.3/2.6秒只是参考测量，不是硬编码sleep。

优先读取：
web/src/components/mercury/{MercuryLayer.tsx,renderer.ts,geometry.ts,palette.ts,timeline.ts,dom.ts,config.ts,useMercuryMotion.ts,useConversationTransit.ts,useReadingFold.ts,readingPresentation.ts}
以及其在HpThread、ChatPane、ConversationSidebar中的最小必要接线和相关CSS。不要把整个R1–R6历史或所有业务模块搬进创作包。

本地Mercury Skill只作为当前实现兼容参考：ui_skill_delete_after_done/mercury-motion-hpagent/SKILL.md。用户最新决定优先，不从旧原型重做一套效果。

二、导出目标：一个打开就能创作的文件

在新的artifacts/product-acceptance/ui-iteration/R6/offline-motion-export/attempt-XX/交付，已有目录不覆盖。

主要文件：MERCURY_CREATIVE.React.Standalone.html。

要求：
1. 真正React驱动，file://双击可运行，React/ReactDOM、必要编译能力、字体/样式/图像等均内嵌；运行时无CDN、网络、服务或登录依赖。
2. 从当前真实呈现实现抽取效果，保留当前视觉与时序作基线，不用相似动画、录像、截图或重新猜画的原型冒充。
3. 不直接交付整站压缩bundle作为创作主体。框架运行时可以压缩；自定义效果源码必须可读、命名清楚、有唯一编辑入口和源码映射。
4. 文件内有清楚的“效果源码区”。创意窗口编辑这一份源代码、保存并刷新即可生效；不得只附一份不参与运行的源码文本，而实际画面继续执行另一份旧bundle。必要时内嵌离线编译/装载器。
5. 先复现当前基线，不自行调颜色、速度、轨迹或材质。具体创意修改由我下一窗口另行给出。

三、场景只保留理解特效所需的上下文

保留左侧三条会话A/B/C、真实DOM阅读区域、静态Composer参照和少量长短不同的中英文内容。尺寸、主区/会话条目坐标和层级应对应当前产品，初始画面不要另做美化。

必须直接可操作：
- 当前会话的收纳/展开；在早/中/晚阶段反向。
- 点击B：A从阅读区收束到A条目；B从B条目展开到阅读区。各自身份和宿主不能混用。
- A→B→C连续快切；最终只呈现最后目标，旧回调不能复活中间会话。
- 一个明确标注的慢目标、加载失败、目标活跃执行、减动/窄屏/宿主不可见场景，用于检查正确降级。

这些由独立、很薄的本地夹具提供，不接入真实API/Store，不引入一套新的产品业务状态机。真实数据等待用独立ready事件表示；视觉不能偷偷压缩或伪造业务就绪。

不导出登录、Workspace、Tasks、文件授权、审批、成果或完整Inspector业务。必要的遮挡/弹层/Inspector边界只能用静态占位说明层级。

四、在文件中明确划出三个职责区

A. HOST / LOCKED：只读宿主与适配层。
负责场景数据、A/B/C身份、目标就绪/失败、最后意图代际、阅读DOM与Composer生命周期、hidden/inert/焦点、固定布局参照、宿主矩形、减动与中断信号。创意窗口不得自行修改。

B. EFFECT / EDITABLE：唯一允许创意开发的区域。
负责时间轴的视觉参数、粒子/流线、轨迹内插、密度、透明度、材质、光学细节、遮罩/相位和宿主接纳呈现。具体改变以我下一窗口指令为准；未指定部分保持基线。

C. REVIEW / NOT FOR PRODUCT：独立评审控件。
可提供播放、暂停、进度、重置、场景选择、基线/候选切换和少量参数调节；不能嵌入产品内容，也不能进入最终迁回范围。控件不进入截图式“阅读内容”。

必须给出简短职责说明和不可编辑边界。创意窗口只需理解B和公开接口，不必理解HpAgent鉴权、路由或后端。

五、为安全迁回建立接口与来源合同

将实际代码需要的输入/输出写清楚，而不是先凭空设计一个与现有工程不兼容的API。

重点保留/映射：
- MercuryTimeline及createMercuryTimeline的progress、velocity、target、goto、advance、finish、settled语义。
- MercuryRenderer的构造、draw(progress, anchors, stage)、quality、clear、dispose等当前实际接口。
- 当前geometry/palette/phase相关数据结构，以及阅读区与实际会话宿主坐标来源。
- 视觉启动/结束/取消事件、必要的视觉回调及对象/代际关联。

若抽取时需要离线适配器，把适配器固定在HOST区，在SOURCE_MAP.json说明原模块→离线模块→预期迁回模块。不要为了便于演示修改真实业务hook。

任何完成回调都只表示“视觉完成”，不表示导航、加载、发送、授权或Run完成。

六、始终保留的约束

- 阅读区域仍是真实、可选择和滚动的DOM，不截图、不克隆消息、不复制Composer。
- 特效不修改会话ID、路由、草稿、附件、消息、Run或权限；不代办发送/停止/重试。
- 同一会话正反向保留固定粒子身份；中途反向继承当前进度/速度；不同会话保留各自归属。
- 保留主星流真正越过主会话区并抵达相应条目内部；不以边缘消失或补一根线伪装抵达。
- 视觉范围受阅读区与通向条目的局部通道约束，不遮挡Composer、导航操作或原生dialog。
- 收纳、展开和切换使用同一组效果参数/时间轴，不再分别藏一套更快参数。
- 隐藏内容不可访问/聚焦；装饰Canvas不接收指针、不进入可访问树。
- 快切、卸载、resize、减动、hidden、宿主失联和异常必须正确清理RAF、timer、Canvas及遮罩，不能卡住内容或旧动画回写。
- 不写全局覆盖样式；样式局限效果根节点/专用命名空间，不调整周边已接受布局、排版或配色体系。

七、导出后的实际验证

至少实际打开file://，验证收纳/展开、反向、A/B/C切换、快切、慢目标、失败、减动、390/780/1280/1680及短屏。检查单一输入节点、真实阅读DOM、宿主归属、无残留遮罩/overlay、无异常或运行时外网请求。

对比当前production与导出文件的同数据关键帧、时间轴/事件及几何；发现不一致先修复导出，不把近似版交给创意窗口。

验证结果明确pass/fail/blocked/not-run；模拟visibility/IME不冒充系统冻结/真机。未测性能不宣称60fps或长期无泄漏。

八、交付与停止

交付：
- MERCURY_CREATIVE.React.Standalone.html，唯一必须带去创意窗口的文件。
- CREATIVE_PROMPT.md：可直接交给创意窗口，重复简明职责/接口/禁止修改区，说明“先打开基线，等我给具体创意要求，不自行扩大范围”。同样说明内嵌在HTML中。
- SOURCE_MAP.json：来源路径、当前文件hash、接口、可编辑区、适配关系。
- BASELINE.md、VALIDATION.md及必要截图/事件记录。
- 原始效果源码与导出文件指纹、明确的原样恢复/重置办法。

HTML内部必须自带创作合同、接口说明和来源摘要，并提供可下载的源码/映射；只带HTML到无仓库的新窗口也能知道职责。

给创意窗口的返回要求：继续交付同格式、可独立运行的完整React HTML；保留唯一效果源码区、HOST桥接和接口；附基线/候选对照、修改项/参数和已测/未测；明确哪些效果源码可迁回、哪些仅是评审夹具。只给视频、截图、不可运行JSX或整站替换文件不算交付。

本窗口结束于“离线React创作包已就绪”。不开始创意改稿，不应用到产品，不接受R6，不提交/推送/部署。新文件带回原工程窗口后，再核对基线、审查效果差异、只迁回必要效果模块并执行产品回归，不能整体覆盖HpAgent或直接替换生产bundle。
