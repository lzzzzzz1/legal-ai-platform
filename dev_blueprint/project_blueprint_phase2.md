# 🚀 Legal AI Platform - 项目蓝图与执行手册 (Phase 2: RAG 增强)

> **To Codex (AI 协同工程师):**
> 你好！我是主导架构的 Antigravity / Tech Lead。我们已经成功跑通了 MVP 的核心闭环（8/8 单元测试已全部通过，表现优秀！）。
> 
> 现在我们正式开启 **Phase 2: RAG (检索增强生成) 法律法规知识库建设**。我们将使用本地 **Qdrant** 向量数据库，并接入百炼平台的向量化模型，让 AI 审查合同时有真实的法律法规依据。

---

## 一、 Phase 2 架构决策与设计

请严格遵守以下新增依赖与配置：

### 1. 新增依赖
- **向量数据库**: **Qdrant** (使用 Docker 部署在本地)
- **Python SDK**: `qdrant-client`
- **Embedding 模型**: 阿里云百炼平台的 **`text-embedding-v3`** (中文召回率高，兼容 OpenAI SDK 格式)

### 2. 向量化数据流 (RAG Data Pipeline)
```
[法规文本] -> 智能切块 (按条目) -> 百炼 text-embedding-v3 -> Qdrant 存储 (Collection: legal_laws)
                                                                 ↑
[用户合同] -> 关键段落提取    -> 百炼 text-embedding-v3 -> Qdrant 检索 -> Top-K 关联法条 -> 注入 Prompt -> qwen-max 审查
```

---

## 二、 你当前的任务清单 (Immediate Tasks)

### Task 4: Qdrant 本地部署与数据灌入 (Ingestion)
- [ ] **Docker 部署 Qdrant**: 编写或修改 `docker-compose.yml`，增加 Qdrant 服务，端口映射 `6333:6333`。
- [ ] **编写灌库脚本** `backend/scripts/ingest_laws.py`:
  - 读取测试法规文件（如 `backend/tests/data/civil_code_sample.txt`，包含民法典合同编核心条文）。
  - 实现**智能分块**：按“第XXX条”作为正则表达式切分，保留条款的完整语义。
  - 调用百炼的 `text-embedding-v3` 生成 1024 维向量。
  - 在 Qdrant 中创建 `legal_laws` 集合，并将向量与 Payload（包含 `law_name`, `article_no`, `content`）批量 upsert 写入。

### Task 5: 后端 RAG 检索与 Prompt 注入
- [ ] **封装检索服务** `backend/app/services/rag_service.py`:
  - 实现 `retrieve_relevant_laws(query_text: str, top_k: int = 3) -> list` 函数。
  - 将审查项或合同关键句向量化，并在 Qdrant 中进行余弦相似度检索，返回关联度最高的法条。
- [ ] **修改审查服务** `backend/app/services/openai_review.py`:
  - 在调用 `qwen-max` 之前，针对提取的合同文本，先通过 `rag_service` 检索相关的法律依据。
  - 修改 `SYSTEM_PROMPT`，注入检索出的法条，并强约束大模型：**在输出修改建议时，必须指明引用的法律法规名称及条文号（例如：根据《民法典》第XXX条...）**。

### Task 6: 前端展示“参考法条”
- [ ] **更新数据 Schema**:
  - 在 `backend/app/schemas/review.py` 的 `ReviewRisk` 中新增字段：`laws: list[str]`（存储本次审查引用的法条列表）。
  - 更新 Mock 数据与单元测试，确保类型系统对齐。
- [ ] **更新前端卡片渲染**:
  - 修改 `frontend/src/App.tsx` 中的风险卡片渲染逻辑。
  - 在卡片底部增加一个展开/折叠的灰色气泡框，展示“**参考法条依据**”。

---

## 三、 提交流程与纪律 (Git Workflow)
1. 请切出新分支 `feature/rag-qdrant-integration` ?
2. 每完成 Task 4、5、6 的一个子项，请务必编写对应的单元测试并 commit。
3. 遇到百炼 Embedding 接口调用频率限制 (Rate Limit) 或 Qdrant 连接问题时，及时停下并与我对齐。

---
**收到请回复：“已理解 Phase 2 蓝图，准备开始执行 Task 4 部署 Qdrant”，并立刻更新 docker-compose.yml。**
