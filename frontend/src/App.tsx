import { ChangeEvent, FormEvent, useMemo, useRef, useState } from "react";

type RiskLevel = "high" | "medium" | "low";

type ReviewRisk = {
  item: string;
  level: RiskLevel;
  risk: string;
  suggestion: string;
  laws: string[];
};

type ReviewResponse = {
  filename: string;
  risks: ReviewRisk[];
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

function getErrorMessage(error: unknown) {
  if (error instanceof Error) {
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

export default function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const canSubmit = useMemo(() => Boolean(file) && !isLoading, [file, isLoading]);

  function clearReview() {
    setFile(null);
    setReview(null);
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    setReview(null);
    setError(null);

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

    try {
      const result = await reviewContract(file);
      setReview(result);
    } catch (submitError) {
      setError(getErrorMessage(submitError));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="workspace">
        <form className="upload-panel" onSubmit={handleSubmit}>
          <p className="eyebrow">Legal AI Platform</p>
          <h1>合同智能审查</h1>
          <p className="intro">上传 .docx 合同，系统会检查关键条款并生成风险提示。</p>

          <label className={`file-drop ${file ? "file-drop-active" : ""}`}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={handleFileChange}
            />
            <span>{file ? "已选择合同" : "选择合同文件"}</span>
            <strong>{file ? file.name : "仅支持 .docx，最大 10 MB"}</strong>
            {file ? <small>{formatFileSize(file.size)}</small> : null}
          </label>

          <div className="button-row">
            <button className="primary-button" type="submit" disabled={!canSubmit}>
              {isLoading ? "审查中..." : review ? "重新审查" : "开始审查"}
            </button>
            {(file || review || error) && !isLoading ? (
              <button className="secondary-button" type="button" onClick={clearReview}>
                清空
              </button>
            ) : null}
          </div>

          {isLoading ? (
            <div className="progress-card" role="status" aria-live="polite">
              <div className="progress-bar" />
              <p>正在解析合同并调用百炼模型，请稍候。</p>
            </div>
          ) : null}

          {error ? <p className="error-message">{error}</p> : null}
        </form>

        <div className="review-panel">
          {!review && !isLoading ? (
            <div className="empty-state">
              <p>请选择一份合同开始审查。</p>
              <span>结果会按风险等级自动排序。</span>
            </div>
          ) : null}

          {isLoading ? (
            <div className="empty-state">
              <p>正在分析合同关键条款...</p>
              <span>通常需要 10 到 60 秒。</span>
            </div>
          ) : null}

          {review ? (
            <section className="result-stack" aria-live="polite">
              <div className="result-header">
                <div>
                  <p className="eyebrow">Review Result</p>
                  <h2>{review.filename}</h2>
                </div>
                <div className="score-summary" aria-label="风险统计">
                  <span className="score-high">高 {riskCounts.high}</span>
                  <span className="score-medium">中 {riskCounts.medium}</span>
                  <span className="score-low">低 {riskCounts.low}</span>
                </div>
              </div>

              <div className="risk-list">
                {sortedRisks.length ? (
                  sortedRisks.map((risk) => (
                    <article className={`risk-card risk-card-${risk.level}`} key={risk.item}>
                      <div className="risk-card-header">
                        <h3>{risk.item}</h3>
                        <span>{levelLabel[risk.level]}</span>
                      </div>
                      <div className="risk-block">
                        <p className="risk-title">风险提示</p>
                        <p>{risk.risk}</p>
                      </div>
                      <div className="suggestion-block">
                        <p className="risk-title">修改建议</p>
                        <p>{risk.suggestion}</p>
                      </div>
                      {risk.laws.length ? (
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
                  ))
                ) : (
                  <div className="no-risk-state">
                    <p>本次未识别到明确风险项。</p>
                    <span>建议仍由法务人员进行最终复核。</span>
                  </div>
                )}
              </div>
            </section>
          ) : null}
        </div>
      </section>
    </main>
  );
}
