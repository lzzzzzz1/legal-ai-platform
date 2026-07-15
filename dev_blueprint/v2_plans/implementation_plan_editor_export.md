# Legal AI Platform - 闭环修改与高保真导出实现方案

## 概述

基于项目当前的 MVP 基础（FastAPI + React），我们需要实现用户“在线修改合同并高保真导出”的核心闭环。
为了保证用户原 Word 格式（字体、字号、页眉页脚）在导出后 100% 不乱，我们采用 **“模板原地文本替换 (In-place Template Text Replacement)”** 方案。

---

## 🛠️ 技术方案设计

### 1. 数据流向设计

```
[用户上传 .docx] -> 后端解析 & 内存缓存原文件
      ↓
[AI 审查] -> 返回风险项，每个风险项必须携带精确的 `original_text`（合同原句）
      ↓
[前端 Tiptap 编辑器] -> 高亮原句，点击“引用修改”时，在编辑器中替换文本
      ↓
[用户修改确认] -> 点击“导出” -> 前端发送修改对账单 `[{original_text, modified_text}]`
      ↓
[后端导出接口] -> 读取缓存的原始 .docx XML，遍历段落/表格，替换文本 -> 吐出新 .docx 二进制
```

### 2. 接口契约更新

#### `POST /api/review` (更新返回格式)
在 AI 返回的 risks 结构中，增加 `original_text` 字段，且要求大模型必须精准提取合同中的那句话，不能有错别字。
```json
{
  "filename": "contract.docx",
  "risks": [
    {
      "item": "签订地点",
      "level": "medium",
      "risk": "未约定明确的签订地点...",
      "suggestion": "建议修改为：于【北京市海淀区】签署。",
      "original_text": "本协议由双方签署。"  // 大模型精准提取的合同原句
    }
  ]
}
```

#### `POST /api/export` (新增导出接口)
- **Content-Type**: `multipart/form-data` (或接收 JSON。因为是无状态服务，最稳妥的做法是：前端在导出时，再次把**原始 .docx 文件**和**修改对账单**一起发给后端，后端即时处理即时下载，避免后端做复杂的 session 文件缓存)
- **Request**:
  - `file: UploadFile` (原始 .docx)
  - `modifications: str` (JSON 格式的字符串对账单，例如：`[{"original": "本协议由双方签署。", "modified": "本协议由双方于【北京市海淀区】签署。"}]`)
- **Response**: 返回修改后的 `.docx` 二进制文件流。

---

## 📅 逐步实现计划

### 阶段一：后端“原样替换”算法与导出 API (2-3 天)
- **目标**：实现不损坏 Word 样式的文本替换和导出。
- **任务**：
  - 在 `backend/app/services/docx_parser.py` 中，编写 `replace_text_in_docx(file_bytes: bytes, modifications: list[dict]) -> bytes` 函数。
  - **核心算法**：
    - 使用 `docx.Document(BytesIO(file_bytes))` 读取。
    - 遍历 `doc.paragraphs`，针对每个段落，若 `original_text` 存在于 `paragraph.text` 中，使用 `replace` 替换其文本。
    - 遍历 `doc.tables`，对每个单元格段落执行同样的操作。
    - *注意*：`python-docx` 直接修改 `paragraph.text` 会丢失该段落内部细粒度样式（如加粗、斜体等单个 Run 样式），但对于整句替换是安全的。为了防格式崩塌，我们在替换时必须采用**“保留段落属性只换文字”**的写法。
  - 在 `backend/app/main.py` 暴露 `POST /api/export` 接口。
  - 编写测试用例 `tests/test_export.py`，上传一个含有“本协议由双方签署”的 docx，发送替换对账单，验证导出的 docx 中文字已被替换，且文件可被 Word 正常打开。

### 阶段二：前端 Tiptap 在线富文本编辑器引入 (2 天)
- **目标**：将只读的文件展示区升级为可实时编辑的 Web 编辑器。
- **任务**：
  - 在前端安装 `@tiptap/react` 和 `@tiptap/starter-kit`。
  - 将 `frontend/src/App.tsx` 右侧只读的文件展示，替换为 Tiptap 编辑器。
  - 后端在 `POST /api/review` 时，同时返回提取的完整合同 HTML（或纯文本），前端将其载入 Tiptap。

### 阶段三：一键“引用修改”交互与导出闭环 (2 天)
- **目标**：打通卡片与编辑器的交互，完成导出。
- **任务**：
  - 当用户点击卡片上的“引用修改”时：
    - 获取该卡片的 `original_text` 和 `suggestion`（修改建议）。
    - 调用 Tiptap API，在编辑器中定位 `original_text` 并将其替换为 `suggestion`。
    - 将此次修改记录到前端状态 `modifications` 数组中（保存 `{original: original_text, modified: suggestion}`）。
  - 用户可以继续在编辑器中进行手动打字修改。
  - 点击“下载导出”按钮，前端将原始 File 对象和 `modifications` 数组打包为 FormData，发送给 `POST /api/export`，触发浏览器下载。

---

## ⚠️ 关键技术避坑指南

1. **AI 提取原句不精确问题**：
   - *问题*：如果合同大段大段，AI 返回的 `original_text` 漏了逗号，后端 `replace` 就会找不到。
   - *解决*：在 `openai_review.py` 的 System Prompt 中增加硬性要求：“`original_text` 必须与提供的合同原文中的字符完全一致，包含标点符号，严禁任何擅自篡改或缩写。”
2. **段落级替换 vs Run级替换**：
   - *问题*：在 `python-docx` 中，直接设置 `paragraph.text = "新文字"` 会清除段落内的所有 `run`（即如果原句里某个词是加粗的，替换后加粗会消失）。
   - *解决*：对于 MVP，直接替换段落文本（丢失局部细粒度样式）是可接受折中。后期升级为：遍历段落的 `runs`，进行局部精确替换，或者在导出时保留样式属性。
