import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";

type RiskLevel = "high" | "medium" | "low";

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
const reviewScopes = ["合同份数", "签订地点", "联系人信息", "税务条款"];
const emptyEditorHtml = "<p>上传并审查合同后，解析出的正文会显示在这里。</p>";
const MISSING_SENTINEL = "【缺失该约定】";

function isMissingClause(originalText: string | undefined | null): boolean {
  if (!originalText) return true;
  const trimmed = originalText.trim();
  return trimmed === "" || trimmed === MISSING_SENTINEL || trimmed === "缺失该约定";
}

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

function textToEditorHtml(text: string) {
  const paragraphs = text
    .split(/\r?\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  if (!paragraphs.length) {
    return emptyEditorHtml;
  }

  return paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("");
}

function normalizeParagraphs(text: string): ParagraphOption[] {
  return text
    .split(/\r?\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => ({
      anchor: paragraph,
      label: paragraph.length > 56 ? `${paragraph.slice(0, 56)}...` : paragraph
    }));
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

  const editor = useEditor({
    extensions: [StarterKit],
    content: emptyEditorHtml,
    editorProps: {
      attributes: {
        "aria-label": "合同正文编辑器",
        class: "contract-editor"
      }
    },
    onUpdate: ({ editor: activeEditor }) => {
      setEditorText(activeEditor.getText({ blockSeparator: "\n" }));
    }
  });

  useEffect(() => {
    if (!editor) {
      return;
    }

    if (review?.contract_text) {
      editor.commands.setContent(textToEditorHtml(review.contract_text));
      setEditorText(review.contract_text);
      return;
    }

    editor.commands.setContent(emptyEditorHtml);
    setEditorText("");
  }, [editor, review?.contract_text]);

  const sortedRisks = useMemo(() => {
    return [...(review?.risks ?? [])].sort((left, right) => {
      return levelOrder[left.level] - levelOrder[right.level];
    });
  }, [review]);

  const riskCounts = useMemo(() => {
    return sortedRisks.reduce(
      (counts, risk) => ({ ...counts, [risk.level]: counts[risk.level] + 1 }),
      { high: 0, medium: 0, low: 0 } satisfies Record<RiskLevel, number>
    );
  }, [sortedRisks]);

  const paragraphOptions = useMemo(() => normalizeParagraphs(editorText), [editorText]);
  const canSubmit = useMemo(() => Boolean(file) && !isLoading, [file, isLoading]);
  const canExport = Boolean(file) && Boolean(review) && modifications.length > 0 && !isExporting;
  const totalRisks = sortedRisks.length;

  function resetEditorState() {
    setModifications([]);
    setEditorNotice(null);
    setEditorText("");
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
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
      element?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function applyMissingSuggestion(risk: ReviewRisk, anchorText: string | null) {
    if (!editor) {
      setError("编辑器尚未准备好，请稍后重试。");
      return;
    }

    const currentText = editor.getText({ blockSeparator: "\n" }) || editorText;
    const anchor = anchorText ?? "";
    const anchorIndex = anchor ? currentText.indexOf(anchor) : -1;
    const insertionIndex = anchorIndex >= 0 ? anchorIndex + anchor.length : currentText.length;
    const nextText =
      currentText.slice(0, insertionIndex) +
      "\n\n" +
      risk.suggestion +
      currentText.slice(insertionIndex);

    editor.commands.setContent(textToEditorHtml(nextText));
    setEditorText(nextText);
    setError(null);
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
    setEditorNotice(anchorIndex >= 0 ? `已在指定段落后追加"${risk.item}"的补充条款。` : `已追加"${risk.item}"的补充条款到合同末尾。`);
    setModifications((previous) => [
      ...previous.filter((item) => item.modified !== risk.suggestion),
      {
        original: MISSING_SENTINEL,
        modified: risk.suggestion,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: anchorText ?? risk.insert_after_text ?? risk.anchor_text ?? null
      }
    ]);

    revealEditorSelection(Math.max(1, insertionIndex + 1), Math.max(1, insertionIndex + risk.suggestion.length + 1));
  }

  function locateRiskInEditor(risk: ReviewRisk) {
    if (!editor) {
      return;
    }

    const currentText = editor.getText({ blockSeparator: "\n" }) || editorText;
    const candidate = isMissingClause(risk.original_text)
      ? risk.insert_after_text ?? risk.anchor_text ?? ""
      : risk.original_text;

    if (!candidate) {
      return;
    }

    const index = currentText.indexOf(candidate);
    if (index < 0) {
      return;
    }

    revealEditorSelection(index + 1, index + candidate.length + 1);
  }

  function applySuggestion(risk: ReviewRisk, riskKey: string) {
    if (!editor) {
      setError("编辑器尚未准备好，请稍后重试。");
      return;
    }

    const missing = isMissingClause(risk.original_text);
    const currentText = editor.getText({ blockSeparator: "\n" }) || editorText;

    if (missing) {
      const anchor = risk.insert_after_text ?? risk.anchor_text ?? "";
      const anchorIndex = anchor ? currentText.indexOf(anchor) : -1;

      if (anchorIndex >= 0) {
        applyMissingSuggestion(risk, anchor);
        return;
      }

      setManualInsertRiskKey(riskKey);
      setManualInsertAfterText(paragraphOptions[0]?.anchor ?? "");
      setEditorNotice(`"${risk.item}" 暂未锁定插入位置，请选择要插入到哪一段后面。`);
      setError(null);
      return;
    }

    const originalIndex = currentText.indexOf(risk.original_text);

    if (originalIndex === -1) {
      setError("未在当前合同正文中找到对应原文，可能已被修改或模型返回的原文不完全一致。");
      return;
    }

    const nextText = currentText.replace(risk.original_text, risk.suggestion);
    editor.commands.setContent(textToEditorHtml(nextText));
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

    revealEditorSelection(Math.max(1, originalIndex + 1), Math.max(1, originalIndex + risk.suggestion.length + 1));
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

  return (
    <main className="app-shell">
      <header className="topbar" aria-label="应用状态">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            LA
          </span>
          <div>
            <strong>Legal AI</strong>
            <span>合同审查工作台</span>
          </div>
        </div>
        <div className="system-strip">
          <span>Qdrant 法规库</span>
          <span>qwen-max 审查</span>
          <span>Tiptap 审阅草稿</span>
        </div>
      </header>

      <section className="workspace" aria-busy={isLoading}>
        <section className="reader-panel">
          <div className="reader-header">
            <div className="reader-copy">
              <span className="status-chip">RAG 已启用</span>
              <h1>上传合同，生成带法条依据的审查结果。</h1>
              <p>左侧作为主阅读区进行审阅和确认，右侧集中展示风险、依据和插入动作。</p>
            </div>

            <form className="review-form review-form-inline" onSubmit={handleSubmit}>
              <label className={`file-drop ${file ? "file-drop-active" : ""}`}>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={handleFileChange}
                />
                <span className="file-kicker">{file ? "已载入合同" : "选择合同文件"}</span>
                <strong>{file ? file.name : "拖入或选择 .docx 文件"}</strong>
                <small>{file ? formatFileSize(file.size) : `最大 ${maxFileSizeMb} MB，审查后可在线引用修改`}</small>
              </label>

              <div className="button-row">
                <button className="primary-button" type="submit" disabled={!canSubmit}>
                  {isLoading ? "审查中" : review ? "重新审查" : "开始审查"}
                </button>
                {(file || review || error) && !isLoading ? (
                  <button className="secondary-button" type="button" onClick={clearReview}>
                    清空
                  </button>
                ) : null}
              </div>
            </form>
          </div>

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
              </div>
              <span>{editorText ? `${editorText.length} 字` : "未载入"}</span>
            </div>
            <div className="editor-page editor-page-promoted">
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

        <aside className="review-sidebar">
          <section className="result-panel">
            <div className="result-header">
              <div>
                <span className="section-label">Review Result</span>
                <h2>{review?.filename ?? "等待合同上传"}</h2>
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

                <div className="risk-list">
                  {sortedRisks.length ? (
                    sortedRisks.map((risk, index) => {
                      const riskKey = `${risk.item}-${index}`;
                      const showManualInsert = manualInsertRiskKey === riskKey && isMissingClause(risk.original_text);

                      return (
                        <article className={`risk-card risk-card-${risk.level}`} key={riskKey}>
                          <div className="risk-card-header">
                            <div>
                              <span>{levelLabel[risk.level]}</span>
                              <h3>{risk.item}</h3>
                            </div>
                            <div className="risk-actions">
                              <button className="secondary-button inline-button" type="button" onClick={() => locateRiskInEditor(risk)}>
                                定位正文
                              </button>
                              <button className={`quote-button${isMissingClause(risk.original_text) ? " quote-append" : ""}`} type="button" onClick={() => applySuggestion(risk, riskKey)}>
                                {isMissingClause(risk.original_text) ? "追加条款" : "引用修改"}
                              </button>
                            </div>
                          </div>

                          <div className={`original-block${isMissingClause(risk.original_text) ? " original-missing" : ""}`}>
                            <p className="risk-title">定位原文</p>
                            <p>{isMissingClause(risk.original_text) ? "合同中缺失该约定，建议补充。" : risk.original_text}</p>
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
                      <p>本次未识别到明确风险项。</p>
                      <span>建议仍由法务人员进行最终复核。</span>
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
