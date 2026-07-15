# Legal AI 合同审查平台 V2.0 技术路径与实施计划 (Implementation Plan)

## 一、技术愿景与核心架构调整

为了满足 [PRD-legal-ai-v2.md](file:///C:/Users/lidongye/.gemini/antigravity/brain/d10d0ddb-1ab9-4ec1-b583-d86ee7a53b81/PRD-legal-ai-v2.md) 中提出的“模糊文本定位”、“修订痕迹展示与导出”和“多合同类型专属审查”需求，V2.0 的技术路径将进行如下架构升级：

```
1. 上传 Word ──> 2. 结构化段落分片 ──> 3. AI 智能分类与要点审查
                                            │
6. 还原导出 (XML修订标记) <── 5. 前端差异渲染 <── 4. 文本相似度模糊匹配定位 (Levenshtein)
```

---

## 二、关键技术点与选型说明

### 1. 文本模糊定位与相似度匹配 (Fuzzy Text Alignment)
*   **难点**：用户在编辑器中可能对 AI 提取的 `original_text` 进行了微调（如修改错别字、更改标点或调换词序），导致 `string.indexOf` 定位失败。
*   **方案**：
    *   **前端匹配**：在前端使用 **Levenshtein 距离（编辑距离）** 或字符级 **Diff-Match-Patch** 算法。
    *   **段落对齐**：计算 AI 返回的 `original_text` 与当前编辑器中所有段落文本的编辑距离比值（Similarity Ratio）。当相似度大于设定的阈值（默认 `0.8`）时，判定为成功定位，并精确计算出匹配子串的字符偏移量（`from` - `to`），以实现 Tiptap 的范围选择高亮和替换。

### 2. Word 原生修订痕迹生成 (OpenXML Track Changes)
*   **难点**：直接对 Docx 段落文本做 `replace` 只能输出普通文本，无法保留 Word 自带的“修订”历史记录。
*   **方案**：
    *   在 Word (OpenXML) 的底层结构中，新增文本被 `<w:ins>` 标签包裹，删除文本被 `<w:del>` 标签包裹。
    *   后端利用 `python-docx` 遍历并操作底层 XML 树元素。例如，将原 `<w:r>` 节点替换为 `<w:del>`，并插入包含修改建议的 `<w:ins>` 节点，并带上修改人（`w:author="Legal AI"`）和时间戳。

---

## 三、拟修改的文件及技术实现路径

### 1. 后端部分

#### [MODIFY] [openai_review.py](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/backend/app/services/openai_review.py)
*   **功能升级**：
    1.  **自动识别合同类型**：修改 Prompt，使 AI 第一步返回 `contract_type`（如采购、劳动、租赁）。
    2.  **动态审查规则库**：根据分类，从规则配置库中读取对应的检查项和参考法条。
    3.  **约束 `original_text` 和 `insert_after_text` 的返回**：对于缺失约定，强制提供合理的合同上下文锚点，以便前端进行精准段落追加。

#### [MODIFY] [docx_modifier.py](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/backend/app/services/docx_modifier.py)
*   **功能升级**：
    1.  重构 `modify_docx_inplace` 函数，引入 **OpenXML 修订写入器**。
    2.  对于普通替换修改，定位到对应的 `<w:p>`（段落）或 `<w:tc>`（表格单元格），移除旧文字的 Run 节点，包裹进 `<w:del>`；同时插入新 Run 节点并包裹进 `<w:ins>`。
    3.  启用文档全局修订开关：在导出的文档 `settings.xml` 中写入 `<w:trackRevisions />`，使得用户打开 Word 时默认开启修订保护。

---

### 2. 前端部分

#### [MODIFY] [App.tsx](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/App.tsx)
*   **功能升级**：
    1.  **模糊匹配定位函数**：实现 `findFuzzyMatch(editorText: string, query: string, threshold: number = 0.8) -> { from: number, to: number, matchedText: string } | null` 算法。
    2.  **红绿对比渲染**：Tiptap 引入 StarterKit 之外的 Mark 组件（如自定义 `AIModificationMark`），引用修改后原文不消失，而是将旧内容标红，新内容标绿并高亮。
    3.  **定制位置追加（手动/自动）**：如果 AI 推荐了 `insert_after_text` 锚点，优先通过模糊定位匹配该锚点并在其后插入新条款；若匹配失败，则弹窗引导用户进行段落选择。
    4.  **本地占位符高亮提示 (Local Linting)**：在前端增加对 `【...】` 格式草稿占位符的正则扫描，并在编辑器内进行高亮提醒，避免遗漏关键信息填充。
    5.  **动态折叠头栏 (Collapsible Header)**：在上传审查完成后，大上传虚线框自动收缩折叠，转换为超扁平的 `.compact-document-bar` 状态栏，归还垂直空间。
    6.  **全屏聚焦模式与侧栏折叠 (Focus Mode & Collapsible Sidebar)**：在右侧栏边缘提供“折叠”按钮，点击后侧边栏向右隐藏；同时主正文区过渡到居中 `800px` 的纸张样式。

#### [MODIFY] [styles.css](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/styles.css)
*   **样式优化**：
    *   增加前端对比痕迹的 CSS 类：`.ins-mark { background: #e6f7ed; color: #1e8a44; text-decoration: none; }`
    *   `.del-mark { background: #fdf2f2; color: #e02424; text-line-through: line-through; }`
    *   增加本地占位符高亮的 CSS 类：`.placeholder-lint-mark { border-bottom: 2px dashed var(--warn); background: rgb(161 92 7 / 6%); }`
    *   增加折叠工具栏、侧栏折叠和全屏聚焦模式的 CSS 布局定义（如 `.compact-document-bar`，`.workspace-focus` 等）

---

## 四、验证与测试规划 (Verification Plan)

### 1. 自动化测试 (Automated Tests)
*   **模糊对齐测试**：编写前端 Jest/Vitest 单元测试，模拟各种带噪声、标点改动、漏字的文本，断言 `findFuzzyMatch` 的召回率和匹配范围准确度。
*   **Word 修订树测试**：编写后端 `test_track_changes.py`，调用 `modify_docx_inplace` 产生修改，用 `python-docx` 解析并断言输出文件中包含符合 `<w:ins>` 规范的 XML 结构。

### 2. 手动集成验证 (Manual Verification)
1.  **合同类型识别验证**：分别上传一份包含“工资、社保”的文档和一份包含“集装箱、物流”的文档，验证系统是否分别自动应用“劳动合同”和“采购合同”审查范围。
2.  **格式防乱套验证**：上传包含复杂目录和表格的 Word 合同，执行追加与修改，导出后打开，检查 Word 的目录链接、大纲级别是否原样保持完好。
