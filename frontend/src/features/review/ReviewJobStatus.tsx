import type { ReviewJob } from "../../api/reviewJobs";

const labels: Record<ReviewJob["status"], string> = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
};

export function ReviewJobStatus({ job }: { job: ReviewJob | null }) {
  if (!job) return null;
  return (
    <div className={`review-job-status review-job-status-${job.status}`} role="status" aria-live="polite">
      <span>深度审查：{labels[job.status]}</span>
      {job.status === "running" || job.status === "queued" ? <span className="review-job-status-dot" aria-hidden="true" /> : null}
    </div>
  );
}
