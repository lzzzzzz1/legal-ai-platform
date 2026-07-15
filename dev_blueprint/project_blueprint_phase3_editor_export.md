# 🚀 Legal AI Platform - 项目蓝图与执行手册 (Phase 3: 闭环编辑与高保真导出)

> **To Codex (AI 协同工程师):**
> 你好！我是主导架构的 Tech Lead。
> 在完成 Phase 2 的 RAG 规整之后，我们开始攻克最核心的业务闭环 —— **在线合同修改与无损格式导出**。
> 
> 请在执行以下 Task 之前，确保你本地已经通过 `pip install python-docx`，并且已经对齐了以下接口设计。

---

## 一、 接口契约更新

### 1. `POST /api/review`
- 大模型返回的 JSON Schema 中，`risks` 数组下的每个对象必须包含 `original_text` 字段，用于在合同原文中进行精确定位。
- 确保 `SYSTEM_PROMPT` 强约束大模型返回原文时不得更改标点符号和空格。

### 2. `POST /api/export` (新建)
- **请求方法**: `POST`
- **请求格式**: `multipart/form-data`
- **请求参数**:
  - `file`: `UploadFile` (用户上传的原始 `.docx` 文件)
  - `modifications`: `str` (JSON 格式的修改对账单字符串，如：`[{"original": "原句", "modified": "新句"}]`)
- **响应**: 返回修改后的 `.docx` 二进制文件流，保留原有的排版格式（字体、字号、页眉页脚）。

---

## 二、 你当前的任务清单 (Immediate Tasks)

### Task 7: 后端原样替换算法与导出 API
- [ ] **编写替换服务** `backend/app/services/docx_modifier.py`:
  - 实现 `modify_docx_inplace(file_bytes: bytes, modifications: list[dict]) -> bytes` 函数。
  - 使用 `docx.Document` 读取原始文件字节流。
  - 遍历 `doc.paragraphs` 和 `doc.tables`。如果 `original_text` 存在于某段落中，将该段落的文字替换为修改后的文本。
  - **避坑约束**：为了保留段落的原有格式，修改时应使用以下安全替换逻辑（只改 Run 里的 Text，不要直接重置段落 Text，否则会丢失行高和边距样式）：
    ```python
    # 示例伪代码：
    for p in doc.paragraphs:
        if original in p.text:
            # 简单实现：保留段落原格式进行文字覆盖
            # 遍历 runs 局部替换，或者直接在 p.runs[0] 覆盖并清除其他 runs
            # 确保最终段落格式样式得以保留
    ```
- [ ] **在 `main.py` 暴露 `/api/export` 接口**:
  - 使用 FastAPI 的 `StreamingResponse` 返回修改后的二进制流，并设置正确的 Headers：
    `Content-Disposition: attachment; filename="reviewed_contract.docx"`
- [ ] **编写测试用例** `backend/tests/test_export_api.py`：
  - 测试上传一个 Word 文档并附带修改对账单，验证返回的 Word 能够被成功解析，且特定字词已被替换。

### Task 8: 前端富文本编辑器 Tiptap 接入
- [ ] **安装依赖**:
  - 在 `frontend` 目录下安装：`npm install @tiptap/react @tiptap/starter-kit`。
- [ ] **集成编辑器**:
  - 在 `frontend/src/App.tsx` 中引入 Tiptap。
  - 当文件上传解析成功后，将合同全文段落载入 Tiptap 编辑器中显示（左侧工作区）。
  - 使用 CSS 调整 Tiptap 的样式，使其看起来像一个现代化的、无边框的 Word 页面。

### Task 9: 一键引用修改与导出闭环
- [ ] **一键引用逻辑**:
  - 在右侧风险卡片中，添加“**引用修改**”按钮。
  - 点击后，前端在 Tiptap 中搜索并高亮对应的 `original_text`，将其替换为 `suggestion`。
  - 在前端组件状态中记录该笔修改：`const [modifications, setModifications] = useState<{original: string, modified: string}[]>([])`。
- [ ] **导出下载逻辑**:
  - 在左侧编辑器下方或控制栏增加“**确认并导出修改版**”按钮。
  - 点击时，前端将原始 `File` 对象和 `modifications` 列表打包为 `FormData` 发送给 `/api/export`。
  - 接收二进制流并使用浏览器自带的 Blob API 触发文件下载。

---

## 三、 提交流程与纪律 (Git Workflow)
1. 请切出新分支 `feature/docx-inplace-edit-export`。
2. 每一个 Task 完成且经过单元测试验证后，请 commit 一次。

---
**收到请回复：“已理解 Phase 3 闭环编辑与导出蓝图，准备开始执行 Task 7 编写后端替换逻辑”。**
