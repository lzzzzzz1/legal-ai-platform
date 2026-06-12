import assert from "node:assert/strict";
import test from "node:test";

import {
  editDistance,
  extractHeadingCandidate,
  findFuzzyMatch,
  getParagraphMatchScore,
  getSimilarity,
  isMissingClause,
  MISSING_SENTINEL,
  normalizeMatchText,
  stripClausePrefix
} from "../.test-dist/reviewUtils.js";

test("editDistance computes small punctuation-safe distance", () => {
  assert.equal(editDistance("Taxes.", "Taxes"), 1);
});

test("getSimilarity treats close phrases as high similarity", () => {
  assert.ok(getSimilarity("Counterparts clause", "Counterparts clause.") > 0.9);
});

test("normalizeMatchText strips punctuation noise", () => {
  assert.equal(normalizeMatchText("Section 10: Notices."), "section 10 notices");
});

test("stripClausePrefix removes numbering prefixes", () => {
  assert.equal(stripClausePrefix("(j) Counterparts. This Agreement"), "Counterparts. This Agreement");
  assert.equal(stripClausePrefix("10. Notices"), "Notices");
});

test("extractHeadingCandidate returns short clause heading", () => {
  assert.equal(extractHeadingCandidate("(j) Counterparts. This Agreement"), "Counterparts");
});

test("getParagraphMatchScore favors matching headings despite numbering differences", () => {
  const score = getParagraphMatchScore(
    "(j) Counterparts. This Agreement may be executed in one or more counterparts.",
    "Counterparts"
  );
  assert.ok(score >= 0.93);
});

test("findFuzzyMatch resolves heading-only query to the right paragraph", () => {
  const fullText = [
    "(i) Representation by Counsel. Each party acts on its own judgment.",
    "(j) Counterparts. This Agreement may be executed in one or more counterparts.",
    "11. Notices. All notices must be in writing."
  ].join("\n");

  const match = findFuzzyMatch(fullText, "Counterparts", 0.8);
  assert.ok(match);
  assert.equal(match?.matchedText, "Counterparts");
  assert.ok(typeof match?.from === "number" && match.from > 0);
});

test("findFuzzyMatch tolerates punctuation and wording drift", () => {
  const fullText = "合同份数：一式两份。\n签订地点：未约定。";
  const match = findFuzzyMatch(fullText, "合同份数：一式两份", 0.7);
  assert.ok(match);
  assert.equal(match?.matchedText, "合同份数：一式两份");
  assert.equal(match?.from, 0);
});

test("isMissingClause recognizes sentinel variations", () => {
  assert.equal(isMissingClause(MISSING_SENTINEL), true);
  assert.equal(isMissingClause("缺失该约定"), true);
  assert.equal(isMissingClause("Counterparts"), false);
});
