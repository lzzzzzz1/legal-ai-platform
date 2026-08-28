import { apiHeaders, getApiErrorMessage } from "./client";
import type { FeedbackDecision, Modification } from "../domain/reviewTypes";

/** Operations used after the review is complete: tracked export and feedback. */
export async function exportReviewedContract(file: File, modifications: Modification[]) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("modifications", JSON.stringify(modifications));
  formData.append("export_mode", "tracked");

  const response = await fetch("/api/export", {
    method: "POST",
    headers: apiHeaders(),
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await getApiErrorMessage(response, `Export request failed with status ${response.status}.`));
  }
  return {
    blob: await response.blob(),
    applied: Number(response.headers.get("X-Review-Applied-Modifications") ?? modifications.length),
    skipped: Number(response.headers.get("X-Review-Skipped-Modifications") ?? 0),
  };
}

export async function recordReviewFeedback(
  filename: string,
  riskItem: string,
  decision: FeedbackDecision,
  correctedSuggestion?: string,
) {
  const response = await fetch("/api/review/feedback", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      filename,
      risk_item: riskItem,
      decision,
      corrected_suggestion: correctedSuggestion ?? null,
    }),
  });
  if (!response.ok) {
    throw new Error(await getApiErrorMessage(response, "复核反馈记录失败。"));
  }
}
