# R6 评审入口修复，整体仍待人审

用户Windows截图显示旧5346连接被拒绝。Linux监听/HTTP200正常，Windows TCP连接失败；进一步直接bind得到WinError10013，netsh excludedportrange列出5286–5385，包含旧5336与5346。没有修改Windows保留端口、全局转发或防火墙规则。

新入口：

- http://127.0.0.1:15346/REVIEW.html — Windows本机回环静态评审，直接读取原attempt-01文件；仅HTTP响应中的旧链接替换为新端口，原离线文件不改写。
- http://127.0.0.1:15336/ — Windows本机回环代理→WSL私有eth0地址5337 Vite production preview→已再次核实的原隔离API+Fake。alice/bob / e2e-password。
- 原attempt-01/offline/REVIEW.Standalone.html仍可独立打开，源码/合同/证据包保留。

241份源码逐文件与attempt-01完全一致，候选指纹3a79275d06c183029952a4380b67f569fb679c4602b5d4eb496f42e8b42b2198。本步只有环境入口脚本/说明/证据，未重跑603测试或冒充新产品验收。Windows端完整评审HTML读取200（约81.8MB）、preview200、真实隔离API登录/me及退出204通过，见evidence/windows-connectivity.json。用户原失败截图与进程信息保留。

Windows15336桥接只用于核实的本机隔离测试入口；将该入口Origin映射到API原配置的5336测试Origin，保持后端配置/凭据/协议不变。HTTP/SSE逐段转发，Cookie与CSRF继续由原API校验；本轮只测试认证和资源访问，不声称Windows真机全场景浏览器已重跑。原135组浏览器与资源结果仍属于attempt-01记录。

## 启动/恢复

Windows Python路径C:\Users\hp\AppData\Local\Programs\Python\Python312。静态服务：在PowerShell运行以下命令（已运行时不重复启动）：

```powershell
& 'C:\Users\hp\AppData\Local\Programs\Python\Python312\pythonw.exe' '\\wsl.localhost\Ubuntu-22.04\home\hp\workspace\HpAgent\artifacts\product-acceptance\ui-iteration\R6\attempt-02-windows-review-entry\scripts\serve-windows.py'
```

隔离API预览恢复：先按attempt-01/HANDOFF核实原容器/网络/DB/Fake与59迁移；仅私有preview缺失时，在仓库根运行python3 artifacts/product-acceptance/ui-iteration/R6/attempt-02-windows-review-entry/scripts/start-private-preview.py。该脚本记录当前WSL eth0地址，读取核实后的容器IP；不要重复启动已有5337，也不能固定相信旧WSL IP。

然后Windows运行pythonw.exe及同一UNC目录下scripts/proxy-windows.py；其上游读取本attempt evidence/private-preview.json，不接产品API。进程PID见windows-server、windows-proxy与private-preview.json；停止时只停止这些已核对归属的PID，不杀日常服务。check-windows.py可在Windows Python运行，检查HTTP/登录/退出；不会调用供应商或外发。

原attempt-01完整离线HTML和源码/证据指纹未改写。新的端口说明更新在总README/R6计划。本步不接受R6，不提交、推送或部署。
