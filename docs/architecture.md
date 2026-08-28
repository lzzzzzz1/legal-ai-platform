# Legal AI Platform 架构说明

## 设计边界

项目以“合同上传 → 审查诉求沟通 → 异步深度审查 → 风险定位与修订 → Word 导出”为唯一主链路。模块之间通过明确的数据模型和 HTTP 契约协作；页面组件不直接承载网络协议、后台任务存储或文档导出实现。

## 后端：`backend/app`

| 目录 / 模块 | 责任 | 不负责 |
| --- | --- | --- |
| `main.py` | FastAPI 组合根、HTTP 路由、鉴权边界、请求/响应编排 | 模型提示词、DOCX 细节、任务持久化细节 |
| `core/runtime.py` | 环境变量校验、后台任务运行参数 | HTTP 路由和业务决策 |
| `schemas/` | API 输入输出模型与边界验证 | 服务编排 |
| `services/contract_overview.py` | 上传后的中性合同概览 | 深度风险修改 |
| `services/intake_chat.py` | 将自然语言沟通收敛成审查标准 | 合同正文导出 |
| `services/deep_review.py`、`openai_review.py` | 风险审查、模型调用与安全回退 | HTTP 状态码与文件上传 |
| `services/review_jobs.py` | 可恢复的异步审查任务存储与工作线程 | 前端轮询 |
| `services/docx_parser.py`、`pdf_parser.py`、`docx_modifier.py` | 文档读取、质量判定和 Word 修订导出 | 审查策略 |

## 前端：`frontend/src`

| 目录 / 模块 | 责任 |
| --- | --- |
| `api/client.ts` | 租户/鉴权请求头与统一 API 错误解码 |
| `api/legalApi.ts` | 合同概览和审查诉求沟通接口 |
| `api/reviewJobs.ts` | 深度审查任务的创建、查询和轮询 |
| `api/reviewActions.ts` | Word 审阅版导出与风险复核反馈 |
| `domain/` | 审查数据模型、服务端响应标准化 |
| `features/intake/` | 上传及审查诉求对话界面 |
| `features/editor/` | 合同正文编辑与导出控制 |
| `features/review/` | 审查进度、风险卡与定位/撤销动作 |
| `hooks/useReviewWorkflow.ts` | 异步审查任务的页面恢复与轮询生命周期 |
| `App.tsx` | 只负责跨功能区工作流编排和状态连接 |

## 约束与演进规则

1. 新接口先定义 `schemas/` 与 `domain/`，再接入服务和页面；避免在组件内猜测服务端字段。
2. 新外部请求只能进入 `api/`，统一继承身份请求头和错误处理。
3. 文档解析、定位、导出必须保留可验证的原文锚点；模型摘要不能直接作为回写依据。
4. 后台任务应当可恢复、可租户隔离，且不得在浏览器刷新后丢失状态。
5. 修改模块内部结构时保持既有 HTTP 路径、字段名及审查流程不变，并先补回归测试。
