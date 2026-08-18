from typing import Literal

from pydantic import BaseModel, Field, model_validator


RiskLevel = Literal["high", "medium", "low"]
ReviewStatus = Literal["complete", "partial", "needs_manual_review"]
CoverageStatus = Literal["checked", "missing", "uncertain"]
CoverageMethod = Literal["model", "rule", "combined"]
RiskEvidenceStatus = Literal["verified", "needs_manual_review"]
NegotiationLevel = Literal["must_modify", "negotiable", "internal_approval", "prohibited"]
PartyRole = Literal["party_a", "party_b", "other"]
ReviewStyle = Literal["protective", "balanced", "material_only"]
DeepReviewState = Literal["completed", "needs_manual_review"]
ConsistencyStatus = Literal["checked", "warning", "not_applicable"]
DocumentQualityStatus = Literal["searchable", "partial", "scanned", "not_applicable"]
PreflightCheckStatus = Literal["passed", "warning"]
PreflightCategory = Literal["structure", "scope", "punctuation", "typo"]


class LawReference(BaseModel):
    label: str
    official_url: str | None = None
    authority: str | None = None
    effectiveness_status: str | None = None


class ReviewRisk(BaseModel):
    item: str = Field(..., description="Reviewed contract topic")
    level: RiskLevel
    original_text: str = Field(..., description="Exact contract text that triggered the risk")
    anchor_text: str | None = Field(default=None, description="Nearby exact text used to locate insertions")
    insert_after_text: str | None = Field(default=None, description="Exact text after which a missing clause should be inserted")
    risk: str
    suggestion: str
    laws: list[str] = Field(default_factory=list, description="Referenced legal articles")
    source: Literal["model", "rule", "combined"] = Field(default="model", description="How this finding was produced")
    evidence_status: RiskEvidenceStatus = Field(
        default="needs_manual_review",
        description="Whether the contract quote and legal citation were verified against available evidence",
    )
    law_references: list[LawReference] = Field(default_factory=list)
    clause_reference: str | None = None
    party_impact: str | None = None
    negotiation_level: NegotiationLevel | None = None
    minimum_acceptable_text: str | None = None


class ReviewCoverage(BaseModel):
    topic: str = Field(..., description="Review topic")
    status: CoverageStatus
    evidence: str | None = Field(default=None, description="Contract text supporting the coverage result")
    method: CoverageMethod = Field(default="model", description="How coverage was determined")


class ReviewConsistencyCheck(BaseModel):
    check: str = Field(..., description="Deterministic consistency check name")
    status: ConsistencyStatus
    evidence: str = Field(default="", description="Contract text or values supporting the check")
    note: str = Field(default="", description="Explanation of the result")


class DocumentQuality(BaseModel):
    kind: Literal["docx", "pdf"]
    status: DocumentQualityStatus
    pages: int | None = None
    extracted_chars: int = 0
    average_chars_per_page: float | None = None
    ocr_detected: bool = False
    note: str = ""


class ContractOverviewDimension(BaseModel):
    """Neutral, source-grounded facts for the pre-review contract portrait."""

    category: str = Field(min_length=1, max_length=60)
    status: Literal["stated", "partial", "not_found"] = "not_found"
    details: list[str] = Field(default_factory=list, max_length=4)


class ContractOverviewPartyResponsibility(BaseModel):
    """Plain-language reading aid, not a legal conclusion."""

    party: str = Field(min_length=1, max_length=100)
    responsibilities: list[str] = Field(default_factory=list, max_length=5)


class ContractOverviewDecisionPoint(BaseModel):
    """Business question a user should answer before the later legal review."""

    topic: str = Field(min_length=1, max_length=80)
    contract_position: str = Field(default="", max_length=500)
    user_question: str = Field(default="", max_length=300)


class ContractOverview(BaseModel):
    """A lightweight, non-legal orientation shown before the user sets a stance."""

    contract_type: str = "通用商务合同"
    summary: str = ""
    parties: list[str] = Field(default_factory=list)
    transaction_subject: str = ""
    key_terms: list[str] = Field(default_factory=list)
    dimensions: list[ContractOverviewDimension] = Field(default_factory=list, max_length=8)
    business_flow: list[str] = Field(default_factory=list, max_length=6)
    party_responsibilities: list[ContractOverviewPartyResponsibility] = Field(default_factory=list, max_length=4)
    decision_points: list[ContractOverviewDecisionPoint] = Field(default_factory=list, max_length=5)
    clarification_questions: list[str] = Field(default_factory=list)
    method: Literal["model", "fallback"] = "fallback"
    warnings: list[str] = Field(default_factory=list)


class ContractOverviewResponse(BaseModel):
    filename: str
    contract_text: str
    overview: ContractOverview
    document_quality: DocumentQuality | None = None


class DocumentPreflightCheck(BaseModel):
    """A lightweight, deterministic document-quality check.

    These findings are intentionally kept outside the legal-risk list: they
    help users clean up a draft before assessing commercial/legal positions.
    """

    category: PreflightCategory
    title: str
    status: PreflightCheckStatus
    evidence: str = ""
    suggestion: str = ""
    original_text: str | None = Field(
        default=None, description="Exact text that can be corrected automatically when auto_fixable is true"
    )
    replacement_text: str | None = Field(
        default=None, description="Replacement text for a deterministic quality correction"
    )
    auto_fixable: bool = Field(
        default=False, description="Whether the system may safely apply the replacement without a legal judgment"
    )


class TextReviewRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    contract_text: str = Field(min_length=1, max_length=400_000)
    review_scope: list[str] = Field(min_length=1)


class DeepReviewSettings(BaseModel):
    party_role: PartyRole
    other_party_role: str = Field(default="", max_length=200)
    transaction_stage: str = Field(default="", max_length=100)
    timeline_urgency: str = Field(default="", max_length=100)
    counterparty_context: str = Field(default="", max_length=100)
    deal_priorities: list[str] = Field(default_factory=list, max_length=6)
    focus_areas: list[str] = Field(default_factory=list, max_length=8)
    review_style: ReviewStyle = "protective"
    contract_type: str = Field(default="", max_length=100)
    special_requirements: list[str] = Field(default_factory=list, max_length=8)
    business_context: str = Field(default="", max_length=2_000)
    non_negotiables: str = Field(default="", max_length=2_000)
    additional_notes: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def require_other_role_description(self) -> "DeepReviewSettings":
        if self.party_role == "other" and not self.other_party_role.strip():
            raise ValueError("other_party_role is required when party_role is other.")
        return self


class DeepReviewRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    contract_text: str = Field(min_length=1, max_length=400_000)
    settings: DeepReviewSettings
    document_quality: DocumentQuality | None = None


class DeepReviewKeyFact(BaseModel):
    item: str
    contract_term: str = ""
    conclusion: str = "待确认"


class DeepReviewNegotiationItem(BaseModel):
    topic: str
    target: str
    minimum_acceptable: str = ""
    owner: str = "法务/业务确认"


class DeepReviewOutput(BaseModel):
    state: DeepReviewState = "needs_manual_review"
    overall_conclusion: Literal["可签", "有条件可签", "不建议签", "待确认"] = "待确认"
    executive_summary: str = ""
    key_facts: list[DeepReviewKeyFact] = Field(default_factory=list)
    missing_clauses: list[str] = Field(default_factory=list)
    negotiation_items: list[DeepReviewNegotiationItem] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    settings_note: str = ""


class ReviewResponse(BaseModel):
    filename: str
    contract_type: str | None = Field(default=None, description="Detected enterprise contract type")
    contract_text: str | None = Field(default=None, description="Plain text extracted from the uploaded contract")
    risks: list[ReviewRisk]
    review_status: ReviewStatus = Field(default="needs_manual_review")
    review_summary: str = Field(default="", description="Evidence-based review summary")
    review_scope: list[str] = Field(default_factory=list, description="Topics included in this review")
    coverage: list[ReviewCoverage] = Field(default_factory=list, description="Coverage result for each review topic")
    warnings: list[str] = Field(default_factory=list, description="Warnings about review completeness")
    review_duration_ms: int | None = Field(default=None, description="Review duration in milliseconds")
    policy_version: str = Field(default="2026.08", description="Review policy version")
    review_method: Literal["model", "rule", "combined", "manual"] = Field(
        default="combined", description="Methods used to produce this review"
    )
    manual_review_required: bool = Field(default=True, description="Whether a human must confirm the result")
    consistency_checks: list[ReviewConsistencyCheck] = Field(
        default_factory=list, description="Deterministic contract consistency checks"
    )
    document_quality: DocumentQuality | None = Field(
        default=None, description="Document text extraction quality and OCR signal"
    )
    preflight_checks: list[DocumentPreflightCheck] = Field(
        default_factory=list,
        description="Deterministic basic quality and contract-framework checks run before detailed review",
    )
    deep_review: DeepReviewOutput | None = None


class ReviewFeedback(BaseModel):
    filename: str
    risk_item: str
    decision: Literal["confirmed", "rejected", "edited"]
    note: str = ""
    corrected_suggestion: str | None = None
