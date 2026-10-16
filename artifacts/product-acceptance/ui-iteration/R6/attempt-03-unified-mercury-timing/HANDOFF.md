# R6本次时序调整交接

用户最新要求切换对话的粒子收束/展开与“展开阅读区”参数一样。已将两入口改为共用createMercuryTimeline，omega=8.5×默认0.35，hostTempo=2。阅读控件原速度不变，切换由23系数降为8.5；路径、颜色、粒子身份、真实等待及业务保护保持。

入口 http://127.0.0.1:15346/REVIEW.html 已更新为本attempt；直接当前React为/review/after/index.html，调整前对照为/review/before/index.html。真实隔离API+Fake http://127.0.0.1:15336/ 读取当前dist，alice/bob / e2e-password。Windows静态服务归属和current-review-root指针保存在attempt-02；重启方法沿用其README，不能退回Windows保留的5346/5336。

完整离线offline/REVIEW.Standalone.html包含当前React、合同/验证/交接和源码证据ZIP；旧attempt-01离线包保持历史版本。内部Linux验证服务为5348，仅绑定127.0.0.1；Windows使用15346。若静态服务恢复，先核对端口归属；本attempt根可运行python3 -m http.server 5348 --bind 127.0.0.1。Windows服务继续由attempt-02/scripts/serve-windows.py读取current-review-root.json。

修改前241份源码/config逐文件与R6 attempt-01一致并备份。最终241快照见SOURCE_FINGERPRINTS.json；implementation.patch只含4文件。没有API/Store/鉴权、Inspector、草稿或附件逻辑修改。恢复前先保存后来差异，只核对恢复这4个文件，不能reset/clean/使用纯HEAD；不要撤销原R6、File/Task/R5未提交成果。

本次实际production Chromium前后时序、24切换、5收纳/Inspector、真实隔离API及603前端测试分别见VALIDATION_RESULTS。历史135组、长时资源计数不冒充本次新测；本次未再做长时性能/真机/OS冻结/供应商或Worker执行。

原R5冻结决定保持历史记录；用户本次明确要求作为后续R6返修生效。R6整体仍待人审，没有自行接受、提交、推送、部署或开启R7。
