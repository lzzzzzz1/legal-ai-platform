import { Mark, mergeAttributes } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  findFuzzyMatch,
  getParagraphMatchScore,
  isMissingClause,
  MISSING_SENTINEL
} from "./reviewUtils";

type RiskLevel = "high" | "medium" | "low";
type RiskFilter = "all" | RiskLevel;

type ReviewRisk = {
  item: string;
  level: RiskLevel;
  original_text: string;
  anchor_text?: string | null;
  insert_after_text?: string | null;
  risk: string;
  suggestion: string;
  laws?: string[];
};

type ReviewResponse = {
  filename: string;
  contract_type?: string | null;
  contract_text?: string | null;
  risks: ReviewRisk[];
};

type Modification = {
  original: string;
  modified: string;
  anchor_text?: string | null;
  insert_after_text?: string | null;
};

type ParagraphOption = {
  anchor: string;
  label: string;
};

type RiskWithKey = {
  risk: ReviewRisk;
  riskKey: string;
};

const DeleteMark = Mark.create({
  name: "deleted",
  parseHTML() {
    return [{ tag: "del" }, { tag: "span.del-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["del", mergeAttributes(HTMLAttributes, { class: "del-mark" }), 0];
  }
});

const InsertMark = Mark.create({
  name: "inserted",
  parseHTML() {
    return [{ tag: "ins" }, { tag: "span.ins-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["ins", mergeAttributes(HTMLAttributes, { class: "ins-mark" }), 0];
  }
});

const PlaceholderLintMark = Mark.create({
  name: "placeholderLint",
  parseHTML() {
    return [{ tag: "span.placeholder-lint-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { class: "placeholder-lint-mark" }), 0];
  }
});

const levelLabel: Record<RiskLevel, string> = {
  high: "高风险",
  medium: "中风险",
  low: "低风险"
};

const levelOrder: Record<RiskLevel, number> = {
  high: 0,
  medium: 1,
  low: 2
};

const maxFileSizeMb = 10;
const maxFileSizeBytes = maxFileSizeMb * 1024 * 1024;
const reviewScopes = ["付款与发票", "交付与验收", "通知与争议", "责任与知识产权"];
const emptyEditorHtml = "<p>上传并审查合同后，解析出的正文会显示在这里。</p>";
const placeholderPattern = /【[^】]+】/g;

function getErrorMessage(error: unknown) {
  if (error instanceof Error) {
    if (error.message === "Not Found") {
      return "导出接口暂未在运行中的后端生效，请重启或重建后端服务后再试。";
    }

    if (error.message.includes("DASHSCOPE_API_KEY")) {
      return "百炼 API Key 未配置或未进入容器，请检查 backend/.env 后重启后端服务。";
    }

    if (error.message.toLowerCase().includes("timeout")) {
      return "模型审查超时，请稍后重试，或适当精简合同内容。";
    }

    return error.message;
  }

  return "审查失败，请稍后重试。";
}

function formatFileSize(size: number) {
  if (size < 1024 * 1024) {
    return `${Math.max(1, Math.round(size / 1024))} KB`;
  }

  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function lintPlaceholders(html: string) {
  return html.replace(placeholderPattern, (match) => `<span class="placeholder-lint-mark">${match}</span>`);
}

function renderPlainTextFragment(text: string) {
  return lintPlaceholders(escapeHtml(text));
}

function textToParagraphs(text: string) {
  return text
    .split(/\r?\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function paragraphsToEditorHtml(paragraphs: string[]) {
  if (!paragraphs.length) {
    return emptyEditorHtml;
  }

  return paragraphs.map((paragraph) => `<p>${renderPlainTextFragment(paragraph)}</p>`).join("");
}

function textToEditorHtml(text: string) {
  return paragraphsToEditorHtml(textToParagraphs(text));
}

function getHtmlParagraphs(html: string) {
  const paragraphs = html.match(/<p\b[^>]*>[\s\S]*?<\/p>/g);
  return paragraphs && paragraphs.length ? paragraphs : [emptyEditorHtml];
}

function normalizeParagraphs(text: string): ParagraphOption[] {
  return textToParagraphs(text).map((paragraph) => ({
    anchor: paragraph,
    label: paragraph.length > 56 ? `${paragraph.slice(0, 56)}...` : paragraph
  }));
}

function getInsertionAnchor(risk: ReviewRisk): string | null {
  return risk.insert_after_text?.trim() || risk.anchor_text?.trim() || null;
}

function getRiskKey(risk: ReviewRisk, index: number) {
  return `${risk.item}-${risk.original_text}-${index}`;
}

function getParagraphMetaFromOffset(text: string, offset: number) {
  const paragraphs = textToParagraphs(text);
  let currentOffset = 0;

  for (let index = 0; index < paragraphs.length; index += 1) {
    const paragraph = paragraphs[index];
    const start = currentOffset;
    const end = start + paragraph.length;
    if (offset <= end) {
      return { index, text: paragraph, start, end, paragraphs };
    }
    currentOffset = end + 1;
  }

  if (!paragraphs.length) {
    return null;
  }

  const lastIndex = paragraphs.length - 1;
  return {
    index: lastIndex,
    text: paragraphs[lastIndex],
    start: Math.max(0, text.length - paragraphs[lastIndex].length),
    end: text.length,
    paragraphs
  };
}

function buildReplacementDiffHtml(paragraphText: string, originalText: string, suggestion: string) {
  const exactIndex = paragraphText.indexOf(originalText);
  if (exactIndex >= 0) {
    const prefix = paragraphText.slice(0, exactIndex);
    const suffix = paragraphText.slice(exactIndex + originalText.length);
    return `<p>${renderPlainTextFragment(prefix)}<del class="del-mark">${renderPlainTextFragment(originalText)}</del><ins class="ins-mark">${renderPlainTextFragment(suggestion)}</ins>${renderPlainTextFragment(suffix)}</p>`;
  }

  return `<p><del class="del-mark">${renderPlainTextFragment(paragraphText)}</del><ins class="ins-mark">${renderPlainTextFragment(suggestion)}</ins></p>`;
}

function buildInsertedParagraphHtml(suggestion: string) {
  return `<p><ins class="ins-mark">${renderPlainTextFragment(suggestion)}</ins></p>`;
}

async function reviewContract(file: File): Promise<ReviewResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/review", {
    method: "POST",
    body: formData
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? "后端服务暂时不可用。");
  }

  return response.json();
}

async function exportReviewedContract(file: File, modifications: Modification[]) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("modifications", JSON.stringify(modifications));

  const response = await fetch("/api/export", {
    method: "POST",
    body: formData
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? "导出失败，请稍后重试。");
  }

  return response.blob();
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export default function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const highlightedParagraphRef = useRef<HTMLElement | null>(null);
  const insertionParagraphRef = useRef<HTMLElement | null>(null);
  const riskCardRefs = useRef<Record<string, HTMLElement | null>>({});

  const [file, setFile] = useState<File | null>(null);
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [modifications, setModifications] = useState<Modification[]>([]);
  const [editorText, setEditorText] = useState("");
  const [editorNotice, setEditorNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualInsertRiskKey, setManualInsertRiskKey] = useState<string | null>(null);
  const [manualInsertAfterText, setManualInsertAfterText] = useState("");
  const [activeRiskKey, setActiveRiskKey] = useState<string | null>(null);
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("all");
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  const sortedRisks = useMemo(() => {
    return [...(review?.risks ?? [])].sort((left, right) => levelOrder[left.level] - levelOrder[right.level]);
  }, [review]);

  const risksWithKeys = useMemo<RiskWithKey[]>(() => {
    return sortedRisks.map((risk, index) => ({ risk, riskKey: getRiskKey(risk, index) }));
  }, [sortedRisks]);

  const filteredRisks = useMemo(() => {
    if (riskFilter === "all") {
      return risksWithKeys;
    }

    return risksWithKeys.filter((entry) => entry.risk.level === riskFilter);
  }, [riskFilter, risksWithKeys]);

  const riskCounts = useMemo(() => {
    return sortedRisks.reduce(
      (counts, risk) => ({ ...counts, [risk.level]: counts[risk.level] + 1 }),
      { high: 0, medium: 0, low: 0 } satisfies Record<RiskLevel, number>
    );
  }, [sortedRisks]);

  const paragraphOptions = useMemo(() => normalizeParagraphs(editorText), [editorText]);
  const canSubmit = Boolean(file) && !isLoading;
  const canExport = Boolean(file) && Boolean(review) && modifications.length > 0 && !isExporting;
  const totalRisks = sortedRisks.length;

  function clearEditorHighlight() {
    if (highlightedParagraphRef.current) {
      highlightedParagraphRef.current.classList.remove("contract-paragraph-highlight");
      highlightedParagraphRef.current = null;
    }
  }

  function clearInsertionHighlight() {
    if (insertionParagraphRef.current) {
      insertionParagraphRef.current.classList.remove("contract-paragraph-insert-target");
      insertionParagraphRef.current = null;
    }
  }

  function applyInsertionHighlight(paragraph: HTMLElement | null) {
    clearInsertionHighlight();
    if (!paragraph) return;
    paragraph.classList.add("contract-paragraph-insert-target");
    insertionParagraphRef.current = paragraph;
  }

  const editor = useEditor(
    {
      extensions: [StarterKit, DeleteMark, InsertMark, PlaceholderLintMark],
      content: emptyEditorHtml,
      editable: false,
      editorProps: {
        attributes: {
          "aria-label": "合同正文编辑器",
          class: "contract-editor"
        },
        handleClick: (_view, _pos, event) => {
          const target = event.target;
          if (!(target instanceof HTMLElement)) {
            return false;
          }

          const paragraph = target.closest("p");
          if (!(paragraph instanceof HTMLElement)) {
            return false;
          }

          const paragraphText = paragraph.innerText.trim();
          if (!paragraphText) {
            return false;
          }

          if (manualInsertRiskKey) {
            setManualInsertAfterText(paragraphText);
            applyInsertionHighlight(paragraph);
            setEditorNotice("已选中插入位置，确认后会把补充条款插入到该段后面。");
            setError(null);
            paragraph.scrollIntoView({ behavior: "smooth", block: "center" });
            return false;
          }

          let bestMatch: RiskWithKey | null = null;
          let bestScore = 0;

          for (const riskEntry of risksWithKeys) {
            const candidate = isMissingClause(riskEntry.risk.original_text)
              ? getInsertionAnchor(riskEntry.risk) ?? ""
              : riskEntry.risk.original_text;
            if (!candidate) continue;

            const score = getParagraphMatchScore(paragraphText, candidate);
            if (score > bestScore) {
              bestScore = score;
              bestMatch = riskEntry;
            }
          }

          if (bestMatch && bestScore >= 0.78) {
            setActiveRiskKey(bestMatch.riskKey);
            riskCardRefs.current[bestMatch.riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
            setEditorNotice(`已在右侧定位到“${bestMatch.risk.item}”风险卡。`);
          }

          return false;
        }
      }
    },
    [manualInsertRiskKey, risksWithKeys]
  );

  useEffect(() => {
    if (!editor) {
      return;
    }

    if (review?.contract_text) {
      const html = textToEditorHtml(review.contract_text);
      editor.commands.setContent(html);
      setEditorText(review.contract_text);
      return;
    }

    editor.commands.setContent(emptyEditorHtml);
    setEditorText("");
  }, [editor, review?.contract_text]);

  function resetEditorState() {
    setModifications([]);
    setEditorNotice(null);
    setEditorText("");
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
    setActiveRiskKey(null);
    setRiskFilter("all");
    setIsSidebarCollapsed(false);
    clearEditorHighlight();
    clearInsertionHighlight();
    editor?.commands.setContent(emptyEditorHtml);
  }

  function clearReview() {
    setFile(null);
    setReview(null);
    setError(null);
    resetEditorState();
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    setReview(null);
    setError(null);
    resetEditorState();

    if (!selectedFile) {
      setFile(null);
      return;
    }

    if (!selectedFile.name.toLowerCase().endsWith(".docx")) {
      setFile(null);
      setError("仅支持上传 .docx 合同文件。请重新选择文件。");
      return;
    }

    if (selectedFile.size > maxFileSizeBytes) {
      setFile(null);
      setError(`文件不能超过 ${maxFileSizeMb} MB。当前文件为 ${formatFileSize(selectedFile.size)}。`);
      return;
    }

    setFile(selectedFile);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!file) {
      setError("请先选择一份 .docx 合同。");
      return;
    }

    setIsLoading(true);
    setError(null);
    setReview(null);
    resetEditorState();

    try {
      const result = await reviewContract(file);
      setReview(result);
    } catch (submitError) {
      setError(getErrorMessage(submitError));
    } finally {
      setIsLoading(false);
    }
  }

  function revealEditorSelection(from: number, to: number) {
    if (!editor) {
      return;
    }

    requestAnimationFrame(() => {
      editor.commands.focus();
      editor.commands.setTextSelection({ from, to });
      const domNode = editor.view.domAtPos(Math.max(0, from - 1)).node as HTMLElement | Text;
      const element = domNode instanceof HTMLElement ? domNode : domNode.parentElement;
      const paragraph = element?.closest("p");

      clearEditorHighlight();
      if (paragraph instanceof HTMLElement) {
        paragraph.classList.add("contract-paragraph-highlight");
        highlightedParagraphRef.current = paragraph;
        paragraph.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }

      element?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function locateRiskInEditor(risk: ReviewRisk) {
    const candidate = isMissingClause(risk.original_text) ? getInsertionAnchor(risk) ?? "" : risk.original_text;
    if (!candidate) {
      return;
    }

    const match = findFuzzyMatch(editorText, candidate, isMissingClause(risk.original_text) ? 0.72 : 0.82);
    if (!match) {
      return;
    }

    revealEditorSelection(match.from + 1, match.to + 1);
  }

  function focusRisk(risk: ReviewRisk, riskKey: string) {
    setActiveRiskKey(riskKey);
    locateRiskInEditor(risk);
    riskCardRefs.current[riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function applyMissingSuggestion(risk: ReviewRisk, anchorText: string | null) {
    if (!editor) {
      setError("编辑器尚未准备好，请稍后重试。");
      return;
    }

    const currentText = editorText;
    const currentHtml = editor.getHTML();
    const htmlParagraphs = getHtmlParagraphs(currentHtml);
    const anchor = anchorText ?? "";
    const fuzzyAnchorMatch = anchor ? findFuzzyMatch(currentText, anchor, 0.72) : null;
    const anchorMeta = fuzzyAnchorMatch ? getParagraphMetaFromOffset(currentText, fuzzyAnchorMatch.from) : null;

    const nextParagraphs = textToParagraphs(currentText);
    const insertAtIndex = anchorMeta ? anchorMeta.index + 1 : nextParagraphs.length;
    nextParagraphs.splice(insertAtIndex, 0, risk.suggestion);

    const nextHtmlParagraphs = [...htmlParagraphs];
    nextHtmlParagraphs.splice(insertAtIndex, 0, buildInsertedParagraphHtml(risk.suggestion));

    editor.commands.setContent(nextHtmlParagraphs.join(""));
    setEditorText(nextParagraphs.join("\n"));
    setError(null);
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
    clearInsertionHighlight();
    setEditorNotice(
      anchorMeta ? `已在指定段落后追加“${risk.item}”的补充条款。` : `已追加“${risk.item}”的补充条款到合同末尾。`
    );
    setModifications((previous) => [
      ...previous.filter((item) => item.modified !== risk.suggestion),
      {
        original: MISSING_SENTINEL,
        modified: risk.suggestion,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: anchorText ?? risk.insert_after_text ?? risk.anchor_text ?? null
      }
    ]);

    const insertedOffset = nextParagraphs.slice(0, insertAtIndex).join("\n").length + (insertAtIndex > 0 ? 1 : 0);
    revealEditorSelection(Math.max(1, insertedOffset + 1), Math.max(1, insertedOffset + risk.suggestion.length + 1));
  }

  function applySuggestion(risk: ReviewRisk, riskKey: string) {
    if (!editor) {
      setError("编辑器尚未准备好，请稍后重试。");
      return;
    }

    const missing = isMissingClause(risk.original_text);
    const currentText = editorText;
    setActiveRiskKey(riskKey);

    if (missing) {
      const anchor = getInsertionAnchor(risk) ?? "";
      const anchorMatch = anchor ? findFuzzyMatch(currentText, anchor, 0.72) : null;

      if (anchorMatch) {
        applyMissingSuggestion(risk, anchorMatch.matchedText);
        return;
      }

      setManualInsertRiskKey(riskKey);
      setManualInsertAfterText(paragraphOptions[0]?.anchor ?? "");
      clearInsertionHighlight();
      setEditorNotice(`“${risk.item}” 暂未锁定插入位置，请选择要插入到哪一段后面。`);
      setError(null);
      return;
    }

    const originalMatch = findFuzzyMatch(currentText, risk.original_text, 0.84);
    if (!originalMatch) {
      setError("未在当前合同正文中找到对应原文，可能已被修改或模型返回的原文不完全一致。");
      return;
    }

    const paragraphMeta = getParagraphMetaFromOffset(currentText, originalMatch.from);
    if (!paragraphMeta) {
      setError("未能定位对应段落，请稍后重试。");
      return;
    }

    const nextText =
      currentText.slice(0, originalMatch.from) + risk.suggestion + currentText.slice(originalMatch.to);
    const currentHtml = editor.getHTML();
    const htmlParagraphs = getHtmlParagraphs(currentHtml);
    const nextHtmlParagraphs = [...htmlParagraphs];
    nextHtmlParagraphs[paragraphMeta.index] = buildReplacementDiffHtml(
      paragraphMeta.text,
      risk.original_text,
      risk.suggestion
    );

    editor.commands.setContent(nextHtmlParagraphs.join(""));
    setEditorText(nextText);
    setError(null);
    setEditorNotice(`已引用“${risk.item}”的修改建议。`);
    setModifications((previous) => [
      ...previous.filter((item) => item.original !== risk.original_text),
      {
        original: risk.original_text,
        modified: risk.suggestion,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: risk.insert_after_text ?? null
      }
    ]);

    revealEditorSelection(Math.max(1, originalMatch.from + 1), Math.max(1, originalMatch.from + risk.suggestion.length + 1));
  }

  async function handleExport() {
    if (!file) {
      setError("请先选择一份 .docx 合同。");
      return;
    }

    if (!modifications.length) {
      setError("请先在右侧风险卡片中引用或追加至少一条修改。");
      return;
    }

    setIsExporting(true);
    setError(null);

    try {
      const blob = await exportReviewedContract(file, modifications);
      downloadBlob(blob, "reviewed_contract.docx");
      setEditorNotice("修改版合同已生成并开始下载。");
    } catch (exportError) {
      setError(getErrorMessage(exportError));
    } finally {
      setIsExporting(false);
    }
  }

  const currentFilename = review?.filename ?? file?.name ?? "未选择合同";
  const currentFileSize = file ? formatFileSize(file.size) : null;

  return (
    <main className="app-shell">
      <header className="topbar" aria-label="应用状态">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            LA
          </span>
          <div>
            <strong>Legal AI</strong>
            <span>企业合同审阅工作台</span>
          </div>
        </div>
        <div className="system-strip">
          <span>Qdrant 法规库</span>
          <span>qwen-max 审查</span>
          <span>Tiptap 审阅草稿</span>
        </div>
      </header>

      <input
        ref={fileInputRef}
        className="hidden-file-input"
        type="file"
        accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        onChange={handleFileChange}
      />

      <section className={`workspace${isSidebarCollapsed ? " workspace-collapsed" : ""}`} aria-busy={isLoading}>
        <button
          className={`sidebar-toggle${isSidebarCollapsed ? " sidebar-toggle-collapsed" : ""}`}
          type="button"
          aria-label={isSidebarCollapsed ? "展开审查侧栏" : "折叠审查侧栏"}
          onClick={() => setIsSidebarCollapsed((value) => !value)}
        >
          {isSidebarCollapsed ? "展开结果" : "收起结果"}
        </button>

        <section className="reader-panel">
          {!review ? (
            <div className="reader-header">
              <div className="reader-copy">
                <span className="status-chip">RAG 已启用</span>
                <h1>上传企业合同，生成带法条依据的审查结果。</h1>
                <p>主区用于审阅正文和修订痕迹，右侧集中展示风险、法条依据与引用动作。</p>
              </div>

              <form className="review-form review-form-inline" onSubmit={handleSubmit}>
                <button
                  className={`file-drop ${file ? "file-drop-active" : ""}`}
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <span className="file-kicker">{file ? "已载入合同" : "选择合同文件"}</span>
                  <strong>{file ? file.name : "拖入或选择 .docx 文件"}</strong>
                  <small>{file ? formatFileSize(file.size) : `最大 ${maxFileSizeMb} MB，审查后可在线引用修改`}</small>
                </button>

                <div className="button-row">
                  <button className="primary-button" type="submit" disabled={!canSubmit}>
                    {isLoading ? "审查中" : "开始审查"}
                  </button>
                  {(file || error) && !isLoading ? (
                    <button className="secondary-button" type="button" onClick={clearReview}>
                      清空
                    </button>
                  ) : null}
                </div>
              </form>
            </div>
          ) : (
            <div className="compact-document-bar">
              <div className="document-info">
                <span className="document-icon" aria-hidden="true">
                  📄
                </span>
                <div>
                  <strong>{currentFilename}</strong>
                  <span className="document-size">
                    {currentFileSize ?? "文件已载入"} · {review.contract_type ?? "通用商务合同"}
                  </span>
                </div>
              </div>
              <div className="compact-document-actions">
                <button className="compact-reupload-btn" type="button" onClick={() => fileInputRef.current?.click()}>
                  重新上传
                </button>
                <button className="secondary-button compact-clear-btn" type="button" onClick={clearReview}>
                  清空
                </button>
                <button className="primary-button compact-review-btn" type="button" disabled={!canSubmit} onClick={(event) => void handleSubmit(event as never)}>
                  {isLoading ? "审查中" : "重新审查"}
                </button>
              </div>
            </div>
          )}

          {isLoading ? (
            <div className="process-panel" role="status" aria-live="polite">
              <div className="progress-bar" />
              <p>正在解析合同、检索法规并调用百炼模型。</p>
            </div>
          ) : null}

          {error ? <p className="error-message">{error}</p> : null}
          {editorNotice ? <p className="success-message">{editorNotice}</p> : null}

          <section className="editor-panel editor-panel-promoted" aria-label="合同正文编辑">
            <div className="editor-heading">
              <div>
                <span className="section-label">Contract Draft</span>
                <h2>合同正文</h2>
                <p className="editor-subtitle">红线删除旧词，绿底高亮新增建议。占位符会以本地提示样式标记，方便复核。</p>
              </div>
              <span>{editorText ? `${editorText.length} 字` : "未载入"}</span>
            </div>

            {manualInsertRiskKey ? (
              <div className="editor-mode-banner" role="status" aria-live="polite">
                正在手动选择插入位置：点击正文中的目标段落，补充条款会插入到该段后面。
              </div>
            ) : null}

            <div className={`editor-page editor-page-promoted${isSidebarCollapsed ? " editor-page-focus" : ""}`}>
              <EditorContent editor={editor} />
            </div>

            <div className="export-row">
              <div>
                <strong>{modifications.length}</strong>
                <span>条已接受修改</span>
              </div>
              <button className="primary-button" type="button" disabled={!canExport} onClick={handleExport}>
                {isExporting ? "导出中" : "确认并导出修改版"}
              </button>
            </div>
          </section>
        </section>

        <aside className={`review-sidebar${isSidebarCollapsed ? " review-sidebar-collapsed" : ""}`}>
          <section className="result-panel">
            <div className="result-header">
              <div>
                <span className="section-label">Review Result</span>
                <h2>{review?.filename ?? "等待合同上传"}</h2>
                {review?.contract_type ? <p className="result-subtitle">合同类型：{review.contract_type}</p> : null}
              </div>
              <div className="score-summary" aria-label="风险统计">
                <span className="score-high">高 {riskCounts.high}</span>
                <span className="score-medium">中 {riskCounts.medium}</span>
                <span className="score-low">低 {riskCounts.low}</span>
              </div>
            </div>

            {!review && !isLoading ? (
              <div className="empty-state">
                <span className="empty-code">READY</span>
                <p>选择一份合同后，审查结果会在这里生成。</p>
                <small>点击风险卡时，正文区会定位到对应条款或锚点。</small>
              </div>
            ) : null}

            {isLoading ? (
              <div className="loading-stack">
                <div className="skeleton-line skeleton-title" />
                <div className="skeleton-card" />
                <div className="skeleton-card skeleton-card-short" />
              </div>
            ) : null}

            {review ? (
              <div className="result-stack" aria-live="polite">
                <div className="summary-band">
                  <div>
                    <span>总风险</span>
                    <strong>{totalRisks}</strong>
                  </div>
                  <div>
                    <span>法规依据</span>
                    <strong>{sortedRisks.reduce((count, risk) => count + (risk.laws?.length ?? 0), 0)}</strong>
                  </div>
                  <div>
                    <span>已接受</span>
                    <strong>{modifications.length}</strong>
                  </div>
                </div>

                <div className="scope-panel sidebar-scope" aria-label="审查范围">
                  <p>本次审查范围</p>
                  <div>
                    {reviewScopes.map((scope) => (
                      <span key={scope}>{scope}</span>
                    ))}
                  </div>
                </div>

                <div className="risk-filter-bar" role="tablist" aria-label="风险筛选">
                  <button
                    className={`filter-chip${riskFilter === "all" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("all")}
                  >
                    全部 {sortedRisks.length}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "high" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("high")}
                  >
                    高风险 {riskCounts.high}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "medium" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("medium")}
                  >
                    中风险 {riskCounts.medium}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "low" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("low")}
                  >
                    低风险 {riskCounts.low}
                  </button>
                </div>

                <div className="risk-list">
                  {filteredRisks.length ? (
                    filteredRisks.map(({ risk, riskKey }) => {
                      const showManualInsert = manualInsertRiskKey === riskKey && isMissingClause(risk.original_text);
                      const accepted = modifications.some(
                        (item) => item.original === risk.original_text || item.modified === risk.suggestion
                      );

                      return (
                        <article
                          ref={(element) => {
                            riskCardRefs.current[riskKey] = element;
                          }}
                          className={`risk-card risk-card-${risk.level}${activeRiskKey === riskKey ? " risk-card-active" : ""}`}
                          key={riskKey}
                        >
                          <div className="risk-card-header">
                            <div>
                              <div className="risk-chip-row">
                                <span>{levelLabel[risk.level]}</span>
                                <span className={`acceptance-chip${accepted ? " acceptance-chip-done" : ""}`}>
                                  {accepted ? "已接受" : "待处理"}
                                </span>
                              </div>
                              <h3>{risk.item}</h3>
                            </div>
                            <div className="risk-actions">
                              <button className="secondary-button inline-button" type="button" onClick={() => focusRisk(risk, riskKey)}>
                                定位正文
                              </button>
                              <button
                                className={`quote-button${isMissingClause(risk.original_text) ? " quote-append" : ""}`}
                                type="button"
                                onClick={() => applySuggestion(risk, riskKey)}
                              >
                                {isMissingClause(risk.original_text) ? "追加条款" : "引用修改"}
                              </button>
                            </div>
                          </div>

                          <div className={`original-block${isMissingClause(risk.original_text) ? " original-missing" : ""}`}>
                            <p className="risk-title">{isMissingClause(risk.original_text) ? "建议插入位置" : "定位原文"}</p>
                            <p>
                              {isMissingClause(risk.original_text)
                                ? getInsertionAnchor(risk) ?? "合同中缺失该约定，暂未锁定明确插入位置，可手动选择段落。"
                                : risk.original_text}
                            </p>
                          </div>

                          <div className="risk-columns">
                            <div className="risk-block">
                              <p className="risk-title">风险提示</p>
                              <p>{risk.risk}</p>
                            </div>
                            <div className="suggestion-block">
                              <p className="risk-title">{isMissingClause(risk.original_text) ? "建议补充条款" : "修改建议"}</p>
                              <p>{risk.suggestion}</p>
                            </div>
                          </div>

                          {showManualInsert ? (
                            <div className="manual-insert-panel">
                              <p className="risk-title">选择插入位置</p>
                              <p className="manual-insert-hint">可以直接点击左侧正文中的目标段落，或在下方列表中选择。</p>
                              <select value={manualInsertAfterText} onChange={(event) => setManualInsertAfterText(event.target.value)}>
                                {paragraphOptions.map((option) => (
                                  <option key={option.anchor} value={option.anchor}>
                                    {option.label}
                                  </option>
                                ))}
                              </select>
                              <div className="manual-insert-actions">
                                <button
                                  className="primary-button"
                                  type="button"
                                  disabled={!manualInsertAfterText}
                                  onClick={() => applyMissingSuggestion(risk, manualInsertAfterText || null)}
                                >
                                  插入到该段后
                                </button>
                                <button
                                  className="secondary-button"
                                  type="button"
                                  onClick={() => {
                                    setManualInsertRiskKey(null);
                                    setManualInsertAfterText("");
                                    clearInsertionHighlight();
                                  }}
                                >
                                  取消
                                </button>
                              </div>
                            </div>
                          ) : null}

                          {risk.laws?.length ? (
                            <details className="law-reference">
                              <summary>参考法条依据</summary>
                              <ul>
                                {risk.laws.map((law) => (
                                  <li key={law}>{law}</li>
                                ))}
                              </ul>
                            </details>
                          ) : null}
                        </article>
                      );
                    })
                  ) : (
                    <div className="no-risk-state">
                      <p>{sortedRisks.length ? "当前筛选条件下暂无风险项。" : "本次未识别到明确风险项。"}</p>
                      <span>{sortedRisks.length ? "可以切换回全部结果继续查看。" : "建议仍由法务人员进行最终复核。"}</span>
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        </aside>
      </section>
    </main>
  );
}
