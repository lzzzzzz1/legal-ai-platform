export type RiskLevel = "high" | "medium" | "low";
export type RiskFilter = "all" | RiskLevel | "pending" | "processed";

export type LawReference = {
  label: string;
  official_url?: string | null;
  authority?: string | null;
  effectiveness_status?: string | null;
};

export type ReviewRisk = {
  item: string;
  level: RiskLevel;
  original_text: string;
  anchor_text?: string | null;
  insert_after_text?: string | null;
  risk: string;
  suggestion: string;
  laws?: string[];
  source?: "model" | "rule" | "combined";
  evidence_status: "verified" | "needs_manual_review";
  law_references: LawReference[];
  clause_reference?: string | null;
  party_impact?: string | null;
  negotiation_level?: "must_modify" | "negotiable" | "internal_approval" | "prohibited" | null;
  minimum_acceptable_text?: string | null;
};

export type ReviewCoverage = {
  topic: string;
  status: "checked" | "missing" | "uncertain";
  evidence?: string | null;
  method?: "model" | "rule" | "combined";
};

export type ReviewConsistencyCheck = {
  check: string;
  status: "checked" | "warning" | "not_applicable";
  evidence: string;
  note: string;
};

export type DocumentQuality = {
  kind: "docx" | "pdf";
  status: "searchable" | "partial" | "scanned" | "not_applicable";
  pages?: number | null;
  extracted_chars: number;
  average_chars_per_page?: number | null;
  ocr_detected: boolean;
  note: string;
};

export type DocumentPreflightCheck = {
  category: "structure" | "scope" | "punctuation" | "typo";
  title: string;
  status: "passed" | "warning";
  evidence: string;
  suggestion: string;
  original_text?: string | null;
  replacement_text?: string | null;
  auto_fixable?: boolean;
};

export type PartyRole = "party_a" | "party_b" | "other";
export type ReviewStyle = "protective" | "balanced" | "material_only";
export type DeepReviewSettings = {
  party_role: PartyRole;
  other_party_role: string;
  transaction_stage: string;
  timeline_urgency: string;
  counterparty_context: string;
  deal_priorities: string[];
  focus_areas: string[];
  review_style: ReviewStyle;
  contract_type: string;
  special_requirements: string[];
  business_context: string;
  non_negotiables: string;
  additional_notes: string[];
};

export type DeepReviewOutput = {
  state: "completed" | "needs_manual_review";
  overall_conclusion: "可签" | "有条件可签" | "不建议签" | "待确认";
  executive_summary: string;
  key_facts: { item: string; contract_term: string; conclusion: string }[];
  missing_clauses: string[];
  negotiation_items: { topic: string; target: string; minimum_acceptable: string; owner: string }[];
  clarification_questions: string[];
  settings_note: string;
};

export type ContractOverview = {
  contract_type: string;
  summary: string;
  parties: string[];
  transaction_subject: string;
  key_terms: string[];
  dimensions: { category: string; status: "stated" | "partial" | "not_found"; details: string[] }[];
  business_flow: string[];
  party_responsibilities: { party: string; responsibilities: string[] }[];
  decision_points: { topic: string; contract_position: string; user_question: string }[];
  clarification_questions: string[];
  method: "model" | "fallback";
  warnings: string[];
};

export type ContractOverviewResponse = {
  filename: string;
  contract_text: string;
  overview: ContractOverview;
  document_quality?: DocumentQuality | null;
};

export type IntakeChatMessage = {
  role: "assistant" | "user";
  content: string;
  quick_replies?: string[];
  suggested_questions?: string[];
};

export type IntakeReviewCriteria = {
  party_role: PartyRole | null;
  other_party_role: string;
  deal_priorities: string[];
  focus_areas: string[];
  review_style: ReviewStyle;
  business_context: string;
  non_negotiables: string;
  special_requirements: string[];
  additional_notes: string[];
};

export type IntakeChatResponse = {
  assistant_message: string;
  quick_replies: string[];
  suggested_questions: string[];
  criteria: IntakeReviewCriteria;
  ready_for_review: boolean;
  source: "model" | "fallback";
  warning?: string | null;
};

export type ReviewResponse = {
  filename: string;
  contract_type?: string | null;
  contract_text?: string | null;
  risks: ReviewRisk[];
  review_status: "complete" | "partial" | "needs_manual_review";
  review_summary: string;
  review_scope: string[];
  coverage: ReviewCoverage[];
  warnings: string[];
  review_duration_ms?: number | null;
  policy_version?: string;
  review_method?: "model" | "rule" | "combined" | "manual";
  manual_review_required?: boolean;
  consistency_checks?: ReviewConsistencyCheck[];
  document_quality?: DocumentQuality | null;
  preflight_checks?: DocumentPreflightCheck[];
  deep_review?: DeepReviewOutput | null;
};

export type Modification = {
  item?: string;
  risk_key?: string;
  original: string;
  modified: string;
  revision_id?: string;
  anchor_text?: string | null;
  insert_after_text?: string | null;
  paragraph_context?: string | null;
};

export type FeedbackDecision = "confirmed" | "rejected" | "edited";
export type PreflightDecision = "confirmed" | "deferred";

export type ParagraphOption = {
  anchor: string;
  label: string;
};

export type RiskWithKey = {
  risk: ReviewRisk;
  riskKey: string;
};

export type RiskLocationCandidate = {
  paragraph: string;
  paragraphIndex: number;
  from: number;
  to: number;
  selectionFrom: number;
  selectionTo: number;
  score: number;
  reason: "exact" | "anchor" | "similar";
  exactOriginal: boolean;
};

export type ReviewStage = "upload" | "intake" | "modification";
// Retained while older intake helpers remain available for saved local state;
// the visible workflow is now driven by IntakeChatMessage instead.
export type IntakeConversationStep = "role" | "objective" | "focus" | "redlines" | "ready";

export type DeepReviewFormSettings = Omit<DeepReviewSettings, "party_role"> & {
  party_role: PartyRole | "";
};
