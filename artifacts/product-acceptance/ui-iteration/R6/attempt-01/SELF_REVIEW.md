# R6 自审与修复

当前仅工程交付，未自行接受。修复保持原业务合同，R5运动/配色/几何和16子页样式源码无改动。

|问题|复现与原因|相称修复|复验|
|---|---|---|---|
|旧登录/注册响应越过退出代际|client定向测试在fetch未结束时reset，旧请求仍返回true并再GET /me|两条认证方法跨await检查原ApiClient generation|reproduced-boundaries 2失败→fixed-boundaries通过；完整603测试|
|目录点击在guard前改筛选|Sidebar/FileList点击目录，在dirtyResourceEditor存在时allFiles/searchMode提前false|完整目录动作交给原requestResourceChange，navigate获准后改筛选；全部文件也经过同一guard|reproduced-boundaries 2失败→组件及浏览器取消/放弃通过|
|改名/移动参数关闭即丢失|修改名称后X立即调用onClose|原生嵌套Surface确认，取消保字段，busy原拦截保留|reproduced-boundaries 1失败→组件和Escape两层浏览器通过|
|新目录名称离开即丢失|原WorkspaceScreen局部state随页面卸载消失|沿用Shell pendingRoute增加目录草稿原因；取消保留，放弃后继续；代际/提交序号防旧结果清新草稿|WorkspaceBoundary组件与production导航/焦点通过|
|未提交上传/另存选择关闭即丢失|选文件或改保存名后X立即调用onClose；新增2测试失败|本地nested Surface确认，仅未提交/可修改选择保护；已提交操作仍由原operations记录管理|reproduced-upload-save-tests→fixed-upload-save，真实API保存及上传浏览器通过|
|登录提交期间仍可改凭据、重复表单事件|提交只有按钮disabled，字段可变且handler无忙碌检查|提交/确认期间字段disabled、aria-busy及handler既有状态检查|同轮连续submit复现2次login（reproduced-login-double）后改用ref提交锁；4项LoginForm测试、失败重试与真实登录通过|

自审继续检查：新字段reset清除；effect卸载清除所属目录guard；放弃目录时增加draftVersion防旧完成清新字段；取消不提前改URL/history/选择；确认放弃重走原navigate，不绕开下一层Task/权限/附件guard；没有后端/API格式改动。

工具失败单列：首次追加测试使用了错误工作目录前缀，文件未写，记录test-harness-path-error；另一次把node路径误写为PATH执行失败，记录reproduced-upload-save.log，不计测试。新测试初版使用Testing Library不支持的exact选项、默认searchMode隐藏新目录字段；已修正测试夹具并通过类型/全量复验。初次浏览器选择器同时匹配侧栏和主列表，桌面又错误等待隐藏的侧栏按钮；保留product-browser-initial证据，定位后限定main和直接检查按钮display；21组最终通过。

旧R5 transit脚本在520ms后再等待目标DOM，实际可跨过900ms数据到达，再断言“尚未in”造成假失败。改用2500ms真实夹具延迟、等待out settled事件后检查未in；请求仍立即、加载等待未被压缩。初次失败保留transit-baseline.log；当前24场景通过。没有因此改R5速度或产品源码。

静态自审：无页面重设计，无新效果；Workspace/Tasks既有信息层级、控件样式与Inspector接力保持。长名称、窄屏、短屏和200%文字截图及主页面真实操作分别记录。工程检查不代替用户审美判断。


逐图复核补修：390×844、200%文字、超长名称使原表格行高3171px，详情按钮垂直居中到y=2070。已保存review/pre-long-fix及long-action前图/DOM坐标；只在workspace.css的表格单元格增加vertical-align:top，按钮y=524且可直接点击，保留完整名称/表格/滚动。该样式只用于Workspace FileList，不涉及Inspector16子页；精确对照见long-actions.json。

资源工具自审：初始transit资源断言错误要求RAF严格相等，原夹具120秒采样器结束后1→0造成假失败；已改为RAF不增长并保持其他计数相等，重新跑完整80次切换。真实API混合脚本初版等entry[data-entering=false]可能仍处于登录检查，已等真正shell与origin卸载；另修正aria-label为“查看…详情”的按钮定位。原失败保留initial/harness-selector证据，不作为产品缺陷或通过。实际142循环181.263秒与80切换186.782秒均通过，无资源增长。计数范围与性能限制见验证报告。
