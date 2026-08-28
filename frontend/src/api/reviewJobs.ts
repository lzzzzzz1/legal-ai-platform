import { apiHeaders, getApiErrorMessage } from "./client";

export type ReviewJobStatus = "queued" | "running" | "succeeded" | "failed";

export type ReviewJob = {
  job_id: string;
  job_type: string;
  status: ReviewJobStatus;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at: string;
  attempt_count: number;
  result?: unknown;
  error?: string | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object";
}

export function normalizeReviewJob(payload: unknown): ReviewJob {
  if (!isRecord(payload) || typeof payload.job_id !== "string") {
    throw new Error("审查任务返回了无法识别的数据。");
  }
  const status = payload.status;
  if (status !== "queued" && status !== "running" && status !== "succeeded" && status !== "failed") {
    throw new Error("审查任务返回了无效状态。");
  }
  return {
    job_id: payload.job_id,
    job_type: typeof payload.job_type === "string" ? payload.job_type : "deep_review",
    status,
    created_at: typeof payload.created_at === "string" ? payload.created_at : "",
    started_at: typeof payload.started_at === "string" ? payload.started_at : null,
    finished_at: typeof payload.finished_at === "string" ? payload.finished_at : null,
    updated_at: typeof payload.updated_at === "string" ? payload.updated_at : "",
    attempt_count: typeof payload.attempt_count === "number" ? payload.attempt_count : 0,
    result: payload.result,
    error: typeof payload.error === "string" ? payload.error : null,
  };
}

export function shouldPollReviewJob(status: ReviewJobStatus): boolean {
  return status === "queued" || status === "running";
}

export { apiHeaders } from "./client";

export async function waitForReviewJob(
  jobId: string,
  options: { intervalMs?: number; signal?: AbortSignal; onUpdate?: (job: ReviewJob) => void } = {},
): Promise<ReviewJob> {
  const intervalMs = Math.max(250, options.intervalMs ?? 2000);
  while (true) {
    if (options.signal?.aborted) throw new DOMException("Review job polling was cancelled.", "AbortError");
    const job = await getReviewJob(jobId);
    options.onUpdate?.(job);
    if (!shouldPollReviewJob(job.status)) return job;
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, intervalMs);
      options.signal?.addEventListener("abort", () => {
        window.clearTimeout(timer);
        reject(new DOMException("Review job polling was cancelled.", "AbortError"));
      }, { once: true });
    });
  }
}

export async function createReviewJob(request: unknown): Promise<ReviewJob> {
  const response = await fetch("/api/review-jobs", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(await getApiErrorMessage(response, `审查任务创建失败（${response.status}）。`));
  }
  return normalizeReviewJob(await response.json());
}

export async function getReviewJob(jobId: string): Promise<ReviewJob> {
  const response = await fetch(`/api/review-jobs/${encodeURIComponent(jobId)}`, {
    headers: apiHeaders(),
  });
  if (!response.ok) {
    throw new Error(await getApiErrorMessage(response, `审查任务查询失败（${response.status}）。`));
  }
  return normalizeReviewJob(await response.json());
}
