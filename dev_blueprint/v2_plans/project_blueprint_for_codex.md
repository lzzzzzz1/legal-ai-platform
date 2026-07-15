# 🚀 Legal AI Platform - 项目蓝图与执行手册 (For Codex)

> **To Codex (AI 协同工程师):** 
> 你好！我是主导架构的 Antigravity / Tech Lead。这是一份**项目全局蓝图与你的当期执行指令**。请在执行任何代码前，仔细阅读以下上下文，确保我们对架构的认知完全对齐。

---

## 一、 项目全局认知 (The Big Picture)

我们正在从零开发一款面向企业法务/律师的 **AI 法律助手平台 (Legal AI Platform)**。
产品的最终愿景是包含：合同智能审查、文件起草、法律问答。

### 📍 当前阶段：MVP (最小可行性产品) - Day 1 到 Day 21
在 MVP 阶段，**我们不搞 RAG，不搞复杂的微服务集群，不微调模型**。我们要用最快的速度跑通一个核心闭环：
> **用户上传 `.docx` 合同 → 提取纯文本 → 调用 GPT-4o API 审查 → 前端展示风险卡片**

---

## 二、 技术栈与架构约束 (Architecture Decisions)

请严格遵守以下技术栈，**未经允许不要引入重量级依赖（如 Redux、K8s 配置、LangChain等）**：

### 后端 (Backend)
- **框架**: Python 3.10+ & **FastAPI**
- **文档解析**: `python-docx` (只需提取纯文本，暂不需要保留原样富格式)
- **AI 对接**: 原生 `openai` Python SDK (使用百炼兼容的 OpenAI 格式，调用 **qwen-max**)
- **架构范式**: 单体应用，RESTful API

### 前端 (Frontend)
- **框架**: **React 18** + TypeScript + Vite
- **样式**: Vanilla CSS 或 CSS Modules (追求简洁现代的 UI，如果需要快速排版可用少量 Tailwind)
- **状态管理**: 原生 React Hooks (`useState`, `useEffect`) 即可，不需要全局状态库

---

## 三、 你当前的任务清单 (Immediate Tasks)

作为执行搭档，我需要你完成以下骨架搭建与核心逻辑。请一步步来，每完成一块核心功能就进行一次 Git Commit。

### Task 1: 仓库初始化与前后端骨架搭建
- [ ] 创建后端 FastAPI 基础骨架 (包含 `main.py`, 跨域 CORS 配置，基本的 `/health` 接口)
- [ ] 创建前端 Vite-React 基础骨架
- [ ] 配置好前后端联调的脚本 (如 `npm run dev` 代理请求到 `localhost:8000`)

### Task 2: 核心链路 - 后端解析与 AI 调用
- [ ] 编写一个 API 端点 `POST /api/review`，接收上传的 `.docx` 文件。
- [ ] 使用 `python-docx` 将上传的文件转换为纯文本字符串。
- [ ] 封装与阿里云千问百炼平台交互的服务（使用 OpenAI 兼容 SDK，Base URL 指向 `https://dashscope.aliyuncs.com/compatible-mode/v1`，调用模型 `qwen-max`），环境配置使用 `DASHSCOPE_API_KEY`，传入合同文本和以下 Prompt 结构，并要求返回 JSON 格式结果：
  > "你是一名资深合同审查律师，请分析以下合同条款，逐项检查：合同份数、签订地点、联系人信息、税务条款。以 JSON 格式输出风险等级(high/medium/low)和修改建议。"

### Task 3: 核心链路 - 前端 UI 展示
- [ ] 编写一个左侧上传文件并展示文件名的组件。
- [ ] 编写一个右侧面板组件，接收并解析后端返回的 JSON 数据。
- [ ] 根据 JSON 中的风险等级，渲染出红色的“风险提示”和绿色的“修改建议”卡片（参考现代 SaaS 风格）。

---

## 四、 协同与提交流程 (Git Workflow)

因为我们共享同一个 Git 仓库，为了避免上下文冲突，请遵守：

1. **工作分支**: 所有的开发请在一个独立分支上进行（如 `feature/mvp-core-loop`），不要直接推 `main`。
2. **Commit 规范**: 每完成上述 Task 清单中的一个 `[ ]`，就提交一次。Commit Message 必须详细说明你改了什么、装了什么包、思路是什么。
3. **遇到阻碍**: 如果遇到如 API 变动、文档解析乱码等需要决策的问题，不要自己瞎猜，**停下来，把情况总结给我（Tech Lead）**。

---
**收到请回复：“已理解全局蓝图与 MVP 约束，准备开始执行 Task 1”，并立刻着手初始化前后端骨架。**
