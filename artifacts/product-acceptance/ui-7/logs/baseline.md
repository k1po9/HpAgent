# UI-7 基线

日期：2026-10-08，Asia/Shanghai。HEAD：e6791e35990ce6da811f6204806def202e770076。
执行开始已有修改：docs/implementation/README.md（UI-7 计划索引）、未跟踪的 docs/implementation/ui-7-unification-cleanup-plan.md；保留并在实施完成后更新状态。
附件：HpAgent_前端_UI_重构方案_v1.0.md，SHA-256 debffa18de54203fa2e7037108b9f0b3f736e6b350516a9c96fd87590217f892。
附件仅作为阶段产品设计补充。其“本次只生成方案”等写作背景不替代本轮用户的 UI-7 实施请求。
独立测试库：hpagent_ui7_e2e_20261008；使用既有 migration/API/worker 三角色，Redis DB11，API8187/Vite5280，文件目录 /tmp/hpagent-ui7-files-20261008。
既有 Docker 业务服务继续运行，未清业务表、未停止服务、未更改持久化模型或 SSE 协议。
