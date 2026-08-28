import assert from "node:assert/strict";
import test from "node:test";

import { normalizeReviewJob, shouldPollReviewJob } from "../.test-dist/reviewJobs.js";

test("normalizes a succeeded review job", () => {
  const job = normalizeReviewJob({ job_id: "j1", status: "succeeded", result: { risks: [] } });
  assert.equal(job.status, "succeeded");
  assert.deepEqual(job.result, { risks: [] });
});

test("polling stops at terminal states", () => {
  assert.equal(shouldPollReviewJob("queued"), true);
  assert.equal(shouldPollReviewJob("running"), true);
  assert.equal(shouldPollReviewJob("succeeded"), false);
  assert.equal(shouldPollReviewJob("failed"), false);
});
