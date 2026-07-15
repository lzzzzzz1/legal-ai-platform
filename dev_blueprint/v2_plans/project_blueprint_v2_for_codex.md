# 🚀 Legal AI Platform V2.0 - 开发者交付蓝图与执行手册 (For Codex)

> **To Codex (AI 协同工程师):** 
> 你好！我是项目 Tech Lead。本项目已经成功跑通了 V1.0 (MVP) 核心闭环（上传 -> AI 审查 -> Tiptap 编辑 -> 导出还原）。
> 现在，我们需要推进 **V2.0 版本（核心定位防错、文档修订痕迹保留、专属合同类型审查）** 的功能落地。这是一份为你量身定制的**开发路径蓝图与具体执行手册**。请在开始编码前仔细阅读。

---

## 一、 项目当前状态与基线 (Project Baseline)

目前项目已经实现了以下架构和功能：
1. **后端 (FastAPI)**：
   * `/api/review` 接口提取 Docx 合同文本，调起 Qdrant 向量检索匹配相关法条，并调用千问大模型 (`qwen-max`) 进行审查，返回结构化 JSON。
   * `/api/export` 接口接收修改清单（`modifications`），将修改内容覆写回原始 Word 二进制并输出下载。
2. **前端 (React + Tiptap)**：
   * 支持拖拽上传，左侧为主阅读与 Tiptap 富文本编辑区，右侧为风险项卡片列表。
   * 支持一键“定位正文”和“引用修改”（替换原文）或“追加条款”（末尾追加）。
3. **验证情况**：
   * 所有后端 pytest 单元测试（15项）已通过。前端 TypeScript 静态编译检查（`tsc --noEmit`）无错误。

---

## 二、 V2.0 核心改动路径与编码指令

请严格按照以下步骤，逐个模块开发、测试并提交：

### Task 1: 后端大模型多合同类型审查与 Schema 扩展

#### 1. 扩展 Schema 定义
在 [review.py](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/backend/app/schemas/review.py) 中，在 `ReviewResponse` 模型里新增字段：
```python
contract_type: str | None = Field(default=None, description="识别出的合同类型")
```

#### 2. 升级 AI 审查 Prompt
在 [openai_review.py](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/backend/app/services/openai_review.py) 中：
1. **重构 `SYSTEM_PROMPT`**：
   * 指导 AI 第一步根据文本特征判定合同类型（如：“劳动合同”、“采购合同”、“房屋租赁合同”或“通用合同”）。
   * 针对不同类型激活不同审查规则：
     * **劳动合同**：审查“工作岗位与地点”、“试用期及工资限制”、“加班及社保缴纳条款”、“竞业限制及补偿金”。
     * **采购合同**：审查“付款账期与比例”、“交货与检验期约定”、“延期交货或违约起赔点”、“知识产权归属”。
     * **房屋租赁合同**：审查“租期与免租期”、“押金退还条件”、“转租权约定”、“物业与水电分摊”、“强制腾退与违约金”。
     * **通用合同/未识别**：审查“合同份数”、“签订地点”、“联系人通知”、“税务调整与开票”。
2. **更新用户 Prompt 约束**：要求 JSON 输出的最外层包含 `"contract_type"` 字段。
3. **更新 `_normalize_review_payload`**：确保从原始 JSON 负载中提取 `contract_type` 并原样组装至返回字典中，防止其丢失。

---

### Task 2: 前端文本模糊对齐与对比痕迹高亮渲染

#### 1. 编写模糊定位算法 (Fuzzy Alignment)
在 [App.tsx](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/App.tsx) 中实现编辑距离算法，应对用户手动微调或标点符号不一致的定位失败：
```typescript
function getSimilarity(s1: string, s2: string): number {
  let longer = s1;
  let shorter = s2;
  if (s1.length < s2.length) {
    longer = s2;
    shorter = s1;
  }
  const longerLength = longer.length;
  if (longerLength === 0) return 1.0;
  return (longerLength - editDistance(longer, shorter)) / longerLength;
}

function editDistance(s1: string, s2: string): number {
  s1 = s1.toLowerCase();
  s2 = s2.toLowerCase();
  const costs = [];
  for (let i = 0; i <= s1.length; i++) {
    let lastValue = i;
    for (let j = 0; j <= s2.length; j++) {
      if (i === 0) costs[j] = j;
      else if (j > 0) {
        let newValue = costs[j - 1];
        if (s1.charAt(i - 1) !== s2.charAt(j - 1)) {
          newValue = Math.min(Math.min(newValue, lastValue), costs[j]) + 1;
        }
        costs[j - 1] = lastValue;
        lastValue = newValue;
      }
    }
    if (i > 0) costs[s2.length] = lastValue;
  }
  return costs[s2.length];
}

export function findFuzzyMatch(fullText: string, query: string, threshold = 0.8) {
  if (!query) return null;
  const exactIdx = fullText.indexOf(query);
  if (exactIdx >= 0) return { from: exactIdx, to: exactIdx + query.length, matchedText: query };

  const paragraphs = fullText.split("\n");
  let bestSim = 0;
  let bestParagraphIndex = -1;
  let currentOffset = 0;
  let bestOffset = -1;

  for (let i = 0; i < paragraphs.length; i++) {
    const p = paragraphs[i];
    if (p.trim().length > 0) {
      const sim = getSimilarity(p, query);
      if (sim > bestSim) {
        bestSim = sim;
        bestParagraphIndex = i;
        bestOffset = currentOffset;
      }
    }
    currentOffset += p.length + 1;
  }

  if (bestSim >= threshold && bestParagraphIndex >= 0) {
    return {
      from: bestOffset,
      to: bestOffset + paragraphs[bestParagraphIndex].length,
      matchedText: paragraphs[bestParagraphIndex]
    };
  }
  return null;
}
```

#### 2. Tiptap 自定义 Mark 渲染对比痕迹
在 [App.tsx](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/App.tsx) 中：
* 创建 `DeleteMark` 和 `InsertMark` 并载入 `useEditor` 的 `extensions` 中：
```typescript
import { Mark, mergeAttributes } from "@tiptap/core";

const DeleteMark = Mark.create({
  name: "deleted",
  parseHTML() { return [{ tag: "del" }, { tag: "span.del-mark" }]; },
  renderHTML({ HTMLAttributes }) { return ["del", mergeAttributes(HTMLAttributes, { class: "del-mark" }), 0]; }
});

const InsertMark = Mark.create({
  name: "inserted",
  parseHTML() { return [{ tag: "ins" }, { tag: "span.ins-mark" }]; },
  renderHTML({ HTMLAttributes }) { return ["ins", mergeAttributes(HTMLAttributes, { class: "ins-mark" }), 0]; }
});
```
* **引用修改动作逻辑改动**：
  点击“引用修改”时，不再直接用 `.replace()` 清空原文，而是将匹配到的段落内容组装成包含 `<del>`（包裹原文）和 `<ins>`（包裹修改建议）的 HTML 段落。
  * 例如：`currentText` 中的某段 `[原文]` 替换为 `<del class="del-mark">[原文]</del><ins class="ins-mark">[修改建议]</ins>`。
* 在 [styles.css](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/styles.css) 中新增修订高亮样式：
```css
.del-mark {
  background-color: #fde8e8;
  color: #9b1c1c;
  text-decoration: line-through;
}
.ins-mark {
  background-color: #def7ec;
  color: #03543f;
  text-decoration: none;
  font-weight: bold;
}
```

#### 3. 本地占位符高亮提示 (Local Linting)
在 [App.tsx](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/App.tsx) 中：
* 编写一个正则解析逻辑，扫描编辑器内的文本是否包含如 `【...】` 等草稿占位符。
* 为 Tiptap 配置一个简单的自定义 Decorator 扩展（或直接正则替换为带 `.placeholder-lint-mark` 样式的 HTML span 节点），在检测到占位符时高亮警示。
* 在 [styles.css](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/styles.css) 中新增高亮样式：
```css
.placeholder-lint-mark {
  border-bottom: 2px dashed var(--warn);
  background-color: rgb(161 92 7 / 6%);
}
```

#### 4. 动态折叠头栏 与 全屏聚焦模式 (Layout Optimization)
在 [App.tsx](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/App.tsx) 中：
* **折叠头栏 (Option 1)**：在 `review` 为非空时，条件渲染替换：隐藏原有大虚线框 `.reader-header`，改渲染超扁平的 `.compact-document-bar` 状态条，展示 `📄 文件名 (大小)` 及 `重新上传` 按钮。
* **侧栏折叠与聚焦模式 (Option 2)**：
  * 新增 React 状态 `isSidebarCollapsed`。
  * 在右侧 `.review-sidebar` 上边缘或侧边栏连接处，提供一个折叠按钮。
  * 点击折叠后，侧边栏向右完全隐藏 (`display: none` 或横向位移隐藏)，同时为 `.workspace` 容器类附加 `.workspace-collapsed` 状态。
  * 处于折叠状态下，主文档编辑器 `.editor-page` 将自动附加 `.editor-page-focus` 类，将其最大宽度设为居中的 `800px` 纸张视图，营造沉浸式无打扰的 Focus 阅读环境。
* 在 [styles.css](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/frontend/src/styles.css) 中新增布局样式：
```css
.compact-document-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface-muted);
  padding: 12px 18px;
  margin-bottom: 12px;
}
.document-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.document-icon {
  font-size: 1.35rem;
}
.document-info div {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.document-info strong {
  color: var(--ink);
  font-size: 0.94rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.document-size {
  color: var(--muted);
  font-size: 0.8rem;
}
.compact-reupload-btn {
  border: 1px solid var(--line-strong);
  background: #ffffff;
  padding: 6px 14px;
  color: var(--ink);
  font-size: 0.82rem;
  font-weight: 800;
  cursor: pointer;
  border-radius: 999px;
  white-space: nowrap;
}
.workspace-collapsed {
  grid-template-columns: 1fr !important;
}
.editor-page-focus {
  max-width: 800px;
  margin: 0 auto;
}
```

---

### Task 3: 后端 Word 原生修订痕迹 (Track Changes) 导出

#### 1. 重构 OpenXML 修订写入
在 [docx_modifier.py](file:///C:/Users/lidongye/Desktop/Codex%20Projects/legal-ai-platform/backend/app/services/docx_modifier.py) 中，当应用修改时：
* 找到目标 `<w:r>`（Run 节点）匹配修改原文。
* 将原有文字的 Run 节点（或其中的一部分）包装在 `<w:del>` 节点下。
* 在其旁边动态插入 `<w:ins>` 节点，其内包含表示修改后建议文字的 `<w:r>` 及 `<w:t>` 节点。
* 设置对应的修订属性（如 `w:author="Legal AI"` 和当前时间 `w:date="2026-06-11T12:00:00Z"`）。

#### 2. 全局启用修订追踪配置
* 在 `modify_docx_inplace` 执行返回前，访问 Word 压缩包中 `word/settings.xml` 部分，将文档全局修订追踪标志 `<w:trackRevisions />` 写入设置，强制开启修订保护。

---

## 三、 测试与验证要求 (Verification)

1. **后端验证**：在 `backend/tests/` 中扩充测试用例：
   * 模拟包含 `contract_type` 的 AI JSON 结果校验。
   * 写入 docx 修改后，通过 `zipfile` 打开导出的 Word 解压检查 `document.xml` 和 `settings.xml` 内是否成功包含 `<w:ins>`、`<w:del>` 和 `<w:trackRevisions />`。
   * 执行测试命令：
     ```bash
     python -m pytest backend -v
     ```
2. **前端验证**：
   * 运行 `npx tsc --noEmit` 确保无 TypeScript 编译异常。
   * 启动服务手动上传租赁和劳动合同，检查分类回显、定位及红绿高亮差异渲染是否完美呈现。

---
**收到本蓝图后，请创建独立开发分支，并按 Task 1 -> Task 2 -> Task 3 的顺序递推实现。每一次完成请保持颗粒度提交 Commit。**
