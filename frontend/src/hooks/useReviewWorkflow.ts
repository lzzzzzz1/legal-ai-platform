import { useCallback, useEffect, useState } from "react";
import { createReviewJob, getReviewJob, waitForReviewJob, type ReviewJob } from "../api/reviewJobs";
import type { ContractOverviewResponse, DeepReviewFormSettings } from "../domain/reviewTypes";

const STORAGE_KEY = "legal-ai-review-job";

export function useReviewWorkflow() {
  const [activeJob, setActiveJob] = useState<ReviewJob | null>(null);

  useEffect(() => {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as { job_id?: string };
      if (!saved.job_id) return;
      void getReviewJob(saved.job_id)
        .then((job) => {
          setActiveJob(job);
          if (job.status === "succeeded" || job.status === "failed") window.localStorage.removeItem(STORAGE_KEY);
        })
        .catch(() => window.localStorage.removeItem(STORAGE_KEY));
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const submitDeepReview = useCallback(async (
    overview: ContractOverviewResponse,
    settings: DeepReviewFormSettings,
  ) => {
    const queued = await createReviewJob({
      filename: overview.filename,
      contract_text: overview.contract_text,
      settings,
      document_quality: overview.document_quality ?? null,
    });
    setActiveJob(queued);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      job_id: queued.job_id,
      filename: overview.filename,
    }));
    const completed = await waitForReviewJob(queued.job_id, { onUpdate: setActiveJob });
    window.localStorage.removeItem(STORAGE_KEY);
    return completed;
  }, []);

  return { activeJob, submitDeepReview };
}
