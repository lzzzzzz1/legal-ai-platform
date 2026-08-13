import { Mark, mergeAttributes } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import { TextStyle } from "@tiptap/extension-text-style";
import Underline from "@tiptap/extension-underline";
import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  findFuzzyMatch,
  getParagraphMatchScore,
  isMissingClause,
  MISSING_SENTINEL
} from "./reviewUtils";

type RiskLevel = "high" | "medium" | "low";
type RiskFilter = "all" | RiskLevel | "pending" | "processed";

type LawReference = {
  label: string;
  official_url?: string | null;
  authority?: string | null;
  effectiveness_status?: string | null;
};

type ReviewRisk = {
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

type ReviewCoverage = {
  topic: string;
  status: "checked" | "missing" | "uncertain";
  evidence?: string | null;
  method?: "model" | "rule" | "combined";
};

type ReviewConsistencyCheck = {
  check: string;
  status: "checked" | "warning" | "not_applicable";
  evidence: string;
  note: string;
};

type DocumentQuality = {
  kind: "docx" | "pdf";
  status: "searchable" | "partial" | "scanned" | "not_applicable";
  pages?: number | null;
  extracted_chars: number;
  average_chars_per_page?: number | null;
  ocr_detected: boolean;
  note: string;
};

type DocumentPreflightCheck = {
  category: "structure" | "scope" | "punctuation" | "typo";
  title: string;
  status: "passed" | "warning";
  evidence: string;
  suggestion: string;
  original_text?: string | null;
  replacement_text?: string | null;
  auto_fixable?: boolean;
};

type PartyRole = "party_a" | "party_b" | "other";
type ReviewStyle = "protective" | "balanced" | "material_only";
type DeepReviewSettings = {
  party_role: PartyRole;
  other_party_role: string;
  focus_areas: string[];
  review_style: ReviewStyle;
  contract_type: string;
  special_requirements: string[];
  business_context: string;
  non_negotiables: string;
};

type DeepReviewOutput = {
  state: "completed" | "needs_manual_review";
  overall_conclusion: "可签" | "有条件可签" | "不建议签" | "待确认";
  executive_summary: string;
  key_facts: { item: string; contract_term: string; conclusion: string }[];
  missing_clauses: string[];
  negotiation_items: { topic: string; target: string; minimum_acceptable: string; owner: string }[];
  clarification_questions: string[];
  settings_note: string;
};

type ReviewResponse = {
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

type SystemStatus = {
  status: string;
  review_model: { configured: boolean; endpoint_configured: boolean; model?: string | null; host?: string | null };
  knowledge_base: { configured: boolean; endpoint_configured: boolean; collection?: string | null; host?: string | null };
  pdf_parser: { endpoint_configured: boolean; host?: string | null };
  reranker: { enabled: boolean; endpoint_configured: boolean; host?: string | null };
};

type Modification = {
  item?: string;
  original: string;
  modified: string;
  anchor_text?: string | null;
  insert_after_text?: string | null;
  preflight_check_index?: number;
};

type PreflightAutoFix = {
  checkIndex: number;
  original: string;
  replacement: string;
  start: number;
};

type FeedbackDecision = "confirmed" | "rejected" | "edited";

type ParagraphOption = {
  anchor: string;
  label: string;
};

type RiskWithKey = {
  risk: ReviewRisk;
  riskKey: string;
};

type ReviewStage = "upload" | "preflight" | "initial" | "deep_ready" | "modification";

type DeepReviewFormSettings = Omit<DeepReviewSettings, "party_role"> & {
  party_role: PartyRole | "";
};

const DeleteMark = Mark.create({
  name: "deleted",
  parseHTML() {
    return [{ tag: "del" }, { tag: "span.del-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["del", mergeAttributes(HTMLAttributes, { class: "del-mark" }), 0];
  }
});

const InsertMark = Mark.create({
  name: "inserted",
  parseHTML() {
    return [{ tag: "ins" }, { tag: "span.ins-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["ins", mergeAttributes(HTMLAttributes, { class: "ins-mark" }), 0];
  }
});

const PlaceholderLintMark = Mark.create({
  name: "placeholderLint",
  parseHTML() {
    return [{ tag: "span.placeholder-lint-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { class: "placeholder-lint-mark" }), 0];
  }
});

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
const deepFocusOptions = ["价格与付款", "交付与验收", "数据与安全", "知识产权", "责任与赔偿", "解除与退出", "争议解决", "全部"];
const deepRequirementOptions = ["控制预付款", "保留验收权", "限制责任", "数据不出境", "禁止 AI 训练", "争议在我方所在地", "保留审计权", "保护品牌与宣传权"];
const reviewScopes = [
  "基础质量与合同框架",
  "主体与签约权限",
  "合同成立与效力",
  "标的与价格",
  "付款与发票",
  "交付与验收",
  "质量与售后",
  "违约与责任",
  "解除与终止",
  "知识产权",
  "保密与数据",
  "合规与许可",
  "通知与送达",
  "争议解决",
  "附件与文本一致性"
];
const emptyEditorHtml = "<p>上传并审查合同后，解析出的正文会显示在这里。</p>";
const placeholderPattern = /【[^】]+】/g;
const unsupportedEditorCharacters = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

function getErrorMessage(error: unknown) {
  if (error instanceof Error) {
    const normalizedMessage = error.message.toLowerCase();

    if (error.message === "Not Found") {
      return "导出接口暂未在运行中的后端生效，请重启或重建后端服务后再试。";
    }

    if (error.message.includes("DASHSCOPE_API_KEY")) {
      return "百炼 API Key 未配置或未进入容器，请检查 backend/.env 后重启后端服务。";
    }

    if (normalizedMessage.includes("could not be located exactly")) {
      return "最终版未生成：有修改无法精确定位到原合同。请在右侧点击“定位”，确认对应段落后重新应用该建议。";
    }

    if (normalizedMessage.includes("deep review model service is temporarily unavailable")) {
      return "深度审查模型当前不可连接，正文保持锁定。请恢复模型服务后重试。";
    }

    if (normalizedMessage.includes("too long for a single deep review request")) {
      return "合同过长，不能只截取前半部分进行深度审查。请按合同章节拆分后分别完成深度审查。";
    }

    if (
      normalizedMessage.includes("timeout")
      || normalizedMessage.includes("timed out")
      || normalizedMessage.includes("504")
      || normalizedMessage.includes("gateway time-out")
    ) {
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

function lintPlaceholders(html: string) {
  return html.replace(placeholderPattern, (match) => `<span class="placeholder-lint-mark">${match}</span>`);
}

function renderPlainTextFragment(text: string) {
  return lintPlaceholders(escapeHtml(text));
}

function textToParagraphs(text: string) {
  return text
    .split(/\r?\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function paragraphsToEditorHtml(paragraphs: string[]) {
  if (!paragraphs.length) {
    return emptyEditorHtml;
  }

  return paragraphs.map((paragraph) => `<p>${renderPlainTextFragment(paragraph)}</p>`).join("");
}

function textToEditorHtml(text: string) {
  return paragraphsToEditorHtml(textToParagraphs(text.replace(unsupportedEditorCharacters, "")));
}

function buildEditorModifications(originalText: string, editedText: string): Modification[] {
  const originalParagraphs = textToParagraphs(originalText);
  const editedParagraphs = textToParagraphs(editedText);
  const modifications: Modification[] = [];
  const commonLength = Math.min(originalParagraphs.length, editedParagraphs.length);

  for (let index = 0; index < commonLength; index += 1) {
    if (originalParagraphs[index] !== editedParagraphs[index]) {
      modifications.push({ original: originalParagraphs[index], modified: editedParagraphs[index] });
    }
  }

  for (let index = commonLength; index < editedParagraphs.length; index += 1) {
    modifications.push({
      original: MISSING_SENTINEL,
      modified: editedParagraphs[index],
      insert_after_text: editedParagraphs[index - 1] ?? null
    });
  }

  return modifications;
}

function applyAutomaticPreflightFixes(text: string, checks: DocumentPreflightCheck[]) {
  let correctedText = text;
  const modifications: Modification[] = [];
  const fixes: PreflightAutoFix[] = [];

  for (const [checkIndex, check] of checks.entries()) {
    if (!check.auto_fixable || !check.original_text || !check.replacement_text) continue;
    const matchIndex = correctedText.indexOf(check.original_text);
    if (matchIndex < 0) continue;
    correctedText = (
      correctedText.slice(0, matchIndex)
      + check.replacement_text
      + correctedText.slice(matchIndex + check.original_text.length)
    );
    modifications.push({
      item: `${check.category === "punctuation" ? "标点修正" : "文字修正"}：${check.title}`,
      original: check.original_text,
      modified: check.replacement_text,
      preflight_check_index: checkIndex,
    });
    fixes.push({
      checkIndex,
      original: check.original_text,
      replacement: check.replacement_text,
      start: matchIndex,
    });
  }

  return { correctedText, modifications, fixes };
}

function getHtmlParagraphs(html: string) {
  const paragraphs = html.match(/<p\b[^>]*>[\s\S]*?<\/p>/g);
  return paragraphs && paragraphs.length ? paragraphs : [emptyEditorHtml];
}

function normalizeParagraphs(text: string): ParagraphOption[] {
  return textToParagraphs(text).map((paragraph) => ({
    anchor: paragraph,
    label: paragraph.length > 56 ? `${paragraph.slice(0, 56)}...` : paragraph
  }));
}

function getInsertionAnchor(risk: ReviewRisk): string | null {
  return risk.insert_after_text?.trim() || risk.anchor_text?.trim() || null;
}

function getRiskKey(risk: ReviewRisk, index: number) {
  return `${risk.item}-${risk.original_text}-${index}`;
}

function getParagraphMetaFromOffset(text: string, offset: number) {
  const paragraphs = textToParagraphs(text);
  let currentOffset = 0;

  for (let index = 0; index < paragraphs.length; index += 1) {
    const paragraph = paragraphs[index];
    const start = currentOffset;
    const end = start + paragraph.length;
    if (offset <= end) {
      return { index, text: paragraph, start, end, paragraphs };
    }
    currentOffset = end + 1;
  }

  if (!paragraphs.length) {
    return null;
  }

  const lastIndex = paragraphs.length - 1;
  return {
    index: lastIndex,
    text: paragraphs[lastIndex],
    start: Math.max(0, text.length - paragraphs[lastIndex].length),
    end: text.length,
    paragraphs
  };
}

function buildReplacementDiffHtml(paragraphText: string, originalText: string, suggestion: string) {
  const exactIndex = paragraphText.indexOf(originalText);
  if (exactIndex >= 0) {
    const prefix = paragraphText.slice(0, exactIndex);
    const suffix = paragraphText.slice(exactIndex + originalText.length);
    return `<p>${renderPlainTextFragment(prefix)}<del class="del-mark">${renderPlainTextFragment(originalText)}</del><ins class="ins-mark">${renderPlainTextFragment(suggestion)}</ins>${renderPlainTextFragment(suffix)}</p>`;
  }

  return `<p><del class="del-mark">${renderPlainTextFragment(paragraphText)}</del><ins class="ins-mark">${renderPlainTextFragment(suggestion)}</ins></p>`;
}

function buildInsertedParagraphHtml(suggestion: string) {
  return `<p><ins class="ins-mark">${renderPlainTextFragment(suggestion)}</ins></p>`;
}

function apiHeaders() {
  const headers: Record<string, string> = {
    "X-Tenant-ID": import.meta.env.VITE_TENANT_ID || "local"
  };
  const apiToken = import.meta.env.VITE_API_AUTH_TOKEN;
  if (apiToken) {
    headers["X-API-Token"] = apiToken;
  }
  return headers;
}

async function reviewContract(file: File, selectedScope: string[]): Promise<ReviewResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("review_scope", JSON.stringify(selectedScope));

  const response = await fetch("/api/review", {
    method: "POST",
    headers: apiHeaders(),
    body: formData
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Review request failed with status ${response.status}.`);
  }

  return normalizeReviewResponse(await response.json(), file.name);
}

async function reviewContractText(filename: string, contractText: string, selectedScope: string[]): Promise<ReviewResponse> {
  const response = await fetch("/api/review/text", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ filename, contract_text: contractText, review_scope: selectedScope })
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Review request failed with status ${response.status}.`);
  }

  return normalizeReviewResponse(await response.json(), filename);
}

async function reviewContractDeeply(filename: string, contractText: string, settings: DeepReviewSettings): Promise<ReviewResponse> {
  const response = await fetch("/api/review/deep", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ filename, contract_text: contractText, settings })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Deep review request failed with status ${response.status}.`);
  }
  return normalizeReviewResponse(await response.json(), filename);
}

function normalizeReviewResponse(payload: unknown, fallbackFilename: string): ReviewResponse {
  if (!payload || typeof payload !== "object") {
    throw new Error("审查服务返回了无法识别的数据。");
  }

  const source = payload as Record<string, unknown>;
  if (typeof source.contract_text !== "string" || !source.contract_text.trim()) {
    throw new Error("审查服务未返回可显示的合同正文。");
  }

  if (!Array.isArray(source.risks)) {
    throw new Error("审查服务返回的风险列表格式不正确。");
  }

  const risks: ReviewRisk[] = source.risks.map((entry, index) => {
    if (!entry || typeof entry !== "object") {
      throw new Error(`第 ${index + 1} 条风险数据格式不正确。`);
    }

    const risk = entry as Record<string, unknown>;
    const level = risk.level;
    if (level !== "high" && level !== "medium" && level !== "low") {
      throw new Error(`第 ${index + 1} 条风险缺少有效等级。`);
    }

    const requiredFields = ["item", "original_text", "risk", "suggestion"] as const;
    for (const field of requiredFields) {
      if (typeof risk[field] !== "string") {
        throw new Error(`第 ${index + 1} 条风险缺少 ${field}。`);
      }
    }

    const rawLaws = risk.laws;
    const laws = Array.isArray(rawLaws)
      ? rawLaws.filter((law): law is string => typeof law === "string")
      : typeof rawLaws === "string"
        ? [rawLaws]
        : [];

    return {
      item: risk.item as string,
      level,
      original_text: risk.original_text as string,
      anchor_text: typeof risk.anchor_text === "string" ? risk.anchor_text : null,
      insert_after_text: typeof risk.insert_after_text === "string" ? risk.insert_after_text : null,
      risk: risk.risk as string,
      suggestion: risk.suggestion as string,
      laws,
      source: risk.source === "rule" || risk.source === "combined" ? risk.source : "model",
      evidence_status: risk.evidence_status === "verified" ? "verified" : "needs_manual_review",
      clause_reference: typeof risk.clause_reference === "string" ? risk.clause_reference : null,
      party_impact: typeof risk.party_impact === "string" ? risk.party_impact : null,
      negotiation_level: risk.negotiation_level === "must_modify" || risk.negotiation_level === "negotiable" || risk.negotiation_level === "internal_approval" || risk.negotiation_level === "prohibited"
        ? risk.negotiation_level
        : null,
      minimum_acceptable_text: typeof risk.minimum_acceptable_text === "string" ? risk.minimum_acceptable_text : null,
      law_references: Array.isArray(risk.law_references)
        ? risk.law_references.flatMap((entry): LawReference[] => {
            if (!entry || typeof entry !== "object") return [];
            const item = entry as Record<string, unknown>;
            if (typeof item.label !== "string") return [];
            return [{
              label: item.label,
              official_url: typeof item.official_url === "string" ? item.official_url : null,
              authority: typeof item.authority === "string" ? item.authority : null,
              effectiveness_status: typeof item.effectiveness_status === "string" ? item.effectiveness_status : null,
            }];
          })
        : []
    };
  });

  const coverage = Array.isArray(source.coverage)
    ? source.coverage.flatMap((entry): ReviewCoverage[] => {
        if (!entry || typeof entry !== "object") return [];
        const item = entry as Record<string, unknown>;
        const status = item.status;
        if (typeof item.topic !== "string" || (status !== "checked" && status !== "missing" && status !== "uncertain")) {
          return [];
        }
        return [{
          topic: item.topic,
          status,
          evidence: typeof item.evidence === "string" ? item.evidence : null,
          method: item.method === "model" || item.method === "combined" ? item.method : "rule"
        }];
      })
    : [];

  return {
    filename: typeof source.filename === "string" && source.filename ? source.filename : fallbackFilename,
    contract_type: typeof source.contract_type === "string" ? source.contract_type : null,
    contract_text: source.contract_text.replace(unsupportedEditorCharacters, ""),
    risks,
    review_status: source.review_status === "complete" || source.review_status === "partial" || source.review_status === "needs_manual_review"
      ? source.review_status
      : "needs_manual_review",
    review_summary: typeof source.review_summary === "string" ? source.review_summary : "",
    review_scope: Array.isArray(source.review_scope)
      ? source.review_scope.filter((item): item is string => typeof item === "string")
      : [],
    coverage,
    warnings: Array.isArray(source.warnings)
      ? source.warnings.filter((item): item is string => typeof item === "string")
      : [],
    review_duration_ms: typeof source.review_duration_ms === "number" ? source.review_duration_ms : null,
    policy_version: typeof source.policy_version === "string" ? source.policy_version : "2026.08",
    review_method: source.review_method === "model" || source.review_method === "rule" || source.review_method === "manual"
      ? source.review_method
      : "combined",
    manual_review_required: source.manual_review_required !== false,
    consistency_checks: Array.isArray(source.consistency_checks)
      ? source.consistency_checks.flatMap((entry): ReviewConsistencyCheck[] => {
          if (!entry || typeof entry !== "object") return [];
          const item = entry as Record<string, unknown>;
          const status = item.status;
          if (typeof item.check !== "string" || (status !== "checked" && status !== "warning" && status !== "not_applicable")) return [];
          return [{
            check: item.check,
            status,
            evidence: typeof item.evidence === "string" ? item.evidence : "",
            note: typeof item.note === "string" ? item.note : "",
          }];
        })
      : [],
    document_quality: source.document_quality && typeof source.document_quality === "object"
      ? (() => {
          const item = source.document_quality as Record<string, unknown>;
          const status = item.status;
          if (item.kind !== "docx" && item.kind !== "pdf") return null;
          if (status !== "searchable" && status !== "partial" && status !== "scanned" && status !== "not_applicable") return null;
          return {
            kind: item.kind,
            status,
            pages: typeof item.pages === "number" ? item.pages : null,
            extracted_chars: typeof item.extracted_chars === "number" ? item.extracted_chars : 0,
            average_chars_per_page: typeof item.average_chars_per_page === "number" ? item.average_chars_per_page : null,
            ocr_detected: item.ocr_detected === true,
            note: typeof item.note === "string" ? item.note : "",
          };
        })()
      : null
    , preflight_checks: Array.isArray(source.preflight_checks)
      ? source.preflight_checks.flatMap((entry): DocumentPreflightCheck[] => {
          if (!entry || typeof entry !== "object") return [];
          const item = entry as Record<string, unknown>;
          const category = item.category;
          const checkStatus = item.status;
          if (
            (category !== "structure" && category !== "punctuation" && category !== "typo")
            || (checkStatus !== "passed" && checkStatus !== "warning")
            || typeof item.title !== "string"
          ) return [];
          return [{
            category,
            title: item.title,
            status: checkStatus,
            evidence: typeof item.evidence === "string" ? item.evidence : "",
            suggestion: typeof item.suggestion === "string" ? item.suggestion : "",
            original_text: typeof item.original_text === "string" ? item.original_text : null,
            replacement_text: typeof item.replacement_text === "string" ? item.replacement_text : null,
            auto_fixable: item.auto_fixable === true,
          }];
        })
      : [],
    deep_review: source.deep_review && typeof source.deep_review === "object"
      ? (() => {
          const item = source.deep_review as Record<string, unknown>;
          const state = item.state;
          const conclusion = item.overall_conclusion;
          if ((state !== "completed" && state !== "needs_manual_review") || (conclusion !== "可签" && conclusion !== "有条件可签" && conclusion !== "不建议签" && conclusion !== "待确认")) return null;
          const toStrings = (value: unknown) => Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
          const keyFacts = Array.isArray(item.key_facts) ? item.key_facts.flatMap((entry) => {
            if (!entry || typeof entry !== "object") return [];
            const fact = entry as Record<string, unknown>;
            if (typeof fact.item !== "string") return [];
            return [{ item: fact.item, contract_term: typeof fact.contract_term === "string" ? fact.contract_term : "", conclusion: typeof fact.conclusion === "string" ? fact.conclusion : "" }];
          }) : [];
          const negotiations = Array.isArray(item.negotiation_items) ? item.negotiation_items.flatMap((entry) => {
            if (!entry || typeof entry !== "object") return [];
            const negotiation = entry as Record<string, unknown>;
            if (typeof negotiation.topic !== "string") return [];
            return [{ topic: negotiation.topic, target: typeof negotiation.target === "string" ? negotiation.target : "", minimum_acceptable: typeof negotiation.minimum_acceptable === "string" ? negotiation.minimum_acceptable : "", owner: typeof negotiation.owner === "string" ? negotiation.owner : "法务/业务确认" }];
          }) : [];
          return { state, overall_conclusion: conclusion, executive_summary: typeof item.executive_summary === "string" ? item.executive_summary : "", key_facts: keyFacts, missing_clauses: toStrings(item.missing_clauses), negotiation_items: negotiations, clarification_questions: toStrings(item.clarification_questions), settings_note: typeof item.settings_note === "string" ? item.settings_note : "" };
        })()
      : null
  };
}

async function fetchSystemStatus(): Promise<SystemStatus> {
  const response = await fetch("/api/system-status", { headers: apiHeaders() });
  if (!response.ok) throw new Error("系统状态暂时不可获取。");
  return response.json() as Promise<SystemStatus>;
}

async function exportReviewedContract(file: File, modifications: Modification[]) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("modifications", JSON.stringify(modifications));
  formData.append("export_mode", "final");

  const response = await fetch("/api/export", {
    method: "POST",
    headers: apiHeaders(),
    body: formData
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Export request failed with status ${response.status}.`);
  }

  return response.blob();
}

async function exportReviewReport(review: ReviewResponse) {
  const response = await fetch("/api/report", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(review)
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Report request failed with status ${response.status}.`);
  }
  return response.blob();
}

async function recordReviewFeedback(
  filename: string,
  riskItem: string,
  decision: FeedbackDecision,
  correctedSuggestion?: string
) {
  const response = await fetch("/api/review/feedback", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      filename,
      risk_item: riskItem,
      decision,
      corrected_suggestion: correctedSuggestion ?? null
    })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? "复核反馈记录失败。");
  }
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
  const highlightedParagraphRef = useRef<HTMLElement | null>(null);
  const insertionParagraphRef = useRef<HTMLElement | null>(null);
  const riskCardRefs = useRef<Record<string, HTMLElement | null>>({});

  const [file, setFile] = useState<File | null>(null);
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [modifications, setModifications] = useState<Modification[]>([]);
  const [editorText, setEditorText] = useState("");
  const [editorNotice, setEditorNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isReportExporting, setIsReportExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualInsertRiskKey, setManualInsertRiskKey] = useState<string | null>(null);
  const [manualInsertAfterText, setManualInsertAfterText] = useState("");
  const [activeRiskKey, setActiveRiskKey] = useState<string | null>(null);
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("all");
  const [riskFeedback, setRiskFeedback] = useState<Record<string, FeedbackDecision>>({});
  const [reviewStage, setReviewStage] = useState<ReviewStage>("upload");
  const [preflightDecisions, setPreflightDecisions] = useState<Record<number, "add" | "skip">>({});
  const [preflightQualityDecisions, setPreflightQualityDecisions] = useState<Record<number, "keep" | "reverted" | "acknowledged">>({});
  const [preflightAutoFixes, setPreflightAutoFixes] = useState<PreflightAutoFix[]>([]);
  const [preflightBaseText, setPreflightBaseText] = useState("");
  const [deepReviewSettings, setDeepReviewSettings] = useState<DeepReviewFormSettings>({
    party_role: "",
    other_party_role: "",
    focus_areas: [],
    review_style: "protective",
    contract_type: "",
    special_requirements: [],
    business_context: "",
    non_negotiables: ""
  });
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isSystemStatusOpen, setIsSystemStatusOpen] = useState(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [systemStatusError, setSystemStatusError] = useState<string | null>(null);
  const syncingEditorRef = useRef(false);

  const sortedRisks = useMemo(() => {
    return [...(review?.risks ?? [])].sort((left, right) => levelOrder[left.level] - levelOrder[right.level]);
  }, [review]);

  const preflightChecks = review?.preflight_checks ?? [];
  const preflightWarnings = useMemo(
    () => preflightChecks.filter((check) => check.status === "warning"),
    [preflightChecks]
  );
  const autoFixablePreflightChecks = useMemo(
    () => preflightChecks.filter((check) => check.auto_fixable && check.original_text && check.replacement_text),
    [preflightChecks]
  );
  const frameworkPreflightChecks = useMemo(
    () => preflightWarnings.filter((check) => check.category === "structure" || check.category === "scope"),
    [preflightWarnings]
  );
  const qualityPreflightChecks = useMemo(
    () => preflightChecks.flatMap((check, index) => (
      check.status === "warning" && check.category !== "structure" && check.category !== "scope"
        ? [{ check, index }]
        : []
    )),
    [preflightChecks]
  );
  const allFrameworkChecksDecided = frameworkPreflightChecks.every((_, index) => Boolean(preflightDecisions[index]));
  const allQualityChecksDecided = qualityPreflightChecks.every(({ index }) => Boolean(preflightQualityDecisions[index]));
  const frameworkAddSelected = frameworkPreflightChecks.some((_, index) => preflightDecisions[index] === "add");
  const canAdvancePreflight = allFrameworkChecksDecided
    && allQualityChecksDecided
    && (!frameworkAddSelected || editorText.trim() !== preflightBaseText.trim());

  const risksWithKeys = useMemo<RiskWithKey[]>(() => {
    return sortedRisks.map((risk, index) => ({ risk, riskKey: getRiskKey(risk, index) }));
  }, [sortedRisks]);

  const filteredRisks = useMemo(() => {
    if (riskFilter === "all") {
      return risksWithKeys;
    }

    if (riskFilter === "pending") {
      return risksWithKeys.filter(({ risk }) => !modifications.some((item) => item.item === risk.item && (item.original === risk.original_text || (isMissingClause(risk.original_text) && item.modified === risk.suggestion))));
    }

    if (riskFilter === "processed") {
      return risksWithKeys.filter(({ risk }) => modifications.some((item) => item.item === risk.item && (item.original === risk.original_text || (isMissingClause(risk.original_text) && item.modified === risk.suggestion))));
    }

    return risksWithKeys.filter((entry) => entry.risk.level === riskFilter);
  }, [modifications, riskFilter, risksWithKeys]);

  const riskCounts = useMemo(() => {
    return sortedRisks.reduce(
      (counts, risk) => ({ ...counts, [risk.level]: counts[risk.level] + 1 }),
      { high: 0, medium: 0, low: 0 } satisfies Record<RiskLevel, number>
    );
  }, [sortedRisks]);

  const processedRiskCount = useMemo(
    () => sortedRisks.filter((risk) => modifications.some((item) => item.item === risk.item && (item.original === risk.original_text || (isMissingClause(risk.original_text) && item.modified === risk.suggestion)))).length,
    [modifications, sortedRisks],
  );

  const reviewProgress = useMemo(() => {
    const total = review?.review_scope.length || reviewScopes.length;
    const checked = review?.coverage.filter((item) => item.status === "checked").length || 0;
    const verified = sortedRisks.filter((risk) => risk.evidence_status === "verified").length;
    return { total, checked, verified, percentage: total ? Math.round((checked / total) * 100) : 0 };
  }, [review, sortedRisks]);

  const paragraphOptions = useMemo(() => normalizeParagraphs(editorText), [editorText]);
  const canSubmit = Boolean(file) && !isLoading;
  const hasEditorChanges = Boolean(review?.contract_text && editorText !== review.contract_text);
  const canExport = reviewStage === "modification" && Boolean(file) && Boolean(review) && (modifications.length > 0 || hasEditorChanges) && !isExporting;
  const totalRisks = sortedRisks.length;

  useEffect(() => {
    if (!isSystemStatusOpen || systemStatus) return;
    void fetchSystemStatus()
      .then(setSystemStatus)
      .catch((statusError) => setSystemStatusError(getErrorMessage(statusError)));
  }, [isSystemStatusOpen, systemStatus]);

  async function handleReportExport() {
    if (!review || isReportExporting) return;
    setIsReportExporting(true);
    try {
      const blob = await exportReviewReport(review);
      downloadBlob(blob, `${review.filename.replace(/\.[^.]+$/, "")}-审查报告.html`);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "审查报告导出失败。");
    } finally {
      setIsReportExporting(false);
    }
  }

  function clearEditorHighlight() {
    if (highlightedParagraphRef.current) {
      highlightedParagraphRef.current.classList.remove("contract-paragraph-highlight");
      highlightedParagraphRef.current = null;
    }
  }

  function clearInsertionHighlight() {
    if (insertionParagraphRef.current) {
      insertionParagraphRef.current.classList.remove("contract-paragraph-insert-target");
      insertionParagraphRef.current = null;
    }
  }

  function applyInsertionHighlight(paragraph: HTMLElement | null) {
    clearInsertionHighlight();
    if (!paragraph) return;
    paragraph.classList.add("contract-paragraph-insert-target");
    insertionParagraphRef.current = paragraph;
  }

  const editor = useEditor(
    {
      extensions: [
        StarterKit,
        TextStyle,
        Color.configure({ types: ["textStyle"] }),
        Highlight.configure({ multicolor: true }),
        Underline,
        DeleteMark,
        InsertMark,
        PlaceholderLintMark
      ],
      content: emptyEditorHtml,
      editable: true,
      onUpdate: ({ editor: updatedEditor }) => {
        if (!syncingEditorRef.current) {
          setEditorText(updatedEditor.getText());
        }
      },
      editorProps: {
        attributes: {
          "aria-label": "合同正文编辑器",
          class: "contract-editor"
        }
      }
    },
    []
  );

  useEffect(() => {
    if (!editor || editor.isDestroyed) {
      return;
    }

    editor.setOptions({
      editorProps: {
        handleClick: (_view, _pos, event) => {
          const target = event.target;
          if (!(target instanceof HTMLElement)) {
            return false;
          }

          const paragraph = target.closest("p");
          if (!(paragraph instanceof HTMLElement)) {
            return false;
          }

          const paragraphText = paragraph.innerText.trim();
          if (!paragraphText) {
            return false;
          }

          if (manualInsertRiskKey) {
            setManualInsertAfterText(paragraphText);
            applyInsertionHighlight(paragraph);
            setEditorNotice("已选中插入位置，确认后会把补充条款插入到该段后面。");
            setError(null);
            paragraph.scrollIntoView({ behavior: "smooth", block: "center" });
            return false;
          }

          let bestMatch: RiskWithKey | null = null;
          let bestScore = 0;

          for (const riskEntry of risksWithKeys) {
            const candidate = isMissingClause(riskEntry.risk.original_text)
              ? getInsertionAnchor(riskEntry.risk) ?? ""
              : riskEntry.risk.original_text;
            if (!candidate) continue;

            const score = getParagraphMatchScore(paragraphText, candidate);
            if (score > bestScore) {
              bestScore = score;
              bestMatch = riskEntry;
            }
          }

          if (bestMatch && bestScore >= 0.78) {
            setActiveRiskKey(bestMatch.riskKey);
            riskCardRefs.current[bestMatch.riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
            setEditorNotice(`已在右侧定位到“${bestMatch.risk.item}”风险卡。`);
          }

          return false;
        }
      }
    });
  }, [editor, manualInsertRiskKey, risksWithKeys]);

  useEffect(() => {
    if (!editor || editor.isDestroyed) {
      return;
    }

    if (review?.contract_text) {
      try {
        const safeContractText = review.contract_text.replace(unsupportedEditorCharacters, "");
        const html = textToEditorHtml(safeContractText);
        syncingEditorRef.current = true;
        editor.commands.setContent(html);
        syncingEditorRef.current = false;
        setEditorText(safeContractText);
      } catch (editorError) {
        console.error("合同正文载入编辑器失败", editorError);
        syncingEditorRef.current = true;
        editor.commands.setContent(emptyEditorHtml);
        syncingEditorRef.current = false;
        setEditorText("");
        setError("审查结果已生成，但合同正文无法载入编辑器。请刷新页面后重新上传该文件。");
      }
      return;
    }

    syncingEditorRef.current = true;
    editor.commands.setContent(emptyEditorHtml);
    syncingEditorRef.current = false;
    setEditorText("");
  }, [editor, review?.contract_text]);

  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    editor.setEditable(reviewStage === "preflight" || reviewStage === "modification");
  }, [editor, reviewStage]);

  function resetEditorState() {
    setModifications([]);
    setEditorNotice(null);
    setEditorText("");
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
    setActiveRiskKey(null);
    setRiskFilter("all");
    setRiskFeedback({});
    setPreflightDecisions({});
    setPreflightQualityDecisions({});
    setPreflightAutoFixes([]);
    setPreflightBaseText("");
    setDeepReviewSettings({
      party_role: "",
      other_party_role: "",
      focus_areas: [],
      review_style: "protective",
      contract_type: "",
      special_requirements: [],
      business_context: "",
      non_negotiables: ""
    });
    setReviewStage("upload");
    setIsSidebarCollapsed(false);
    clearEditorHighlight();
    clearInsertionHighlight();
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

  function handleFileSelectionLegacy(selectedFile: File | null) {
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

  function handleFileSelection(selectedFile: File | null) {
    setReview(null);
    setError(null);
    resetEditorState();

    if (!selectedFile) {
      setFile(null);
      return;
    }

    const lowerName = selectedFile.name.toLowerCase();
    if (!lowerName.endsWith(".docx") && !lowerName.endsWith(".pdf")) {
      setFile(null);
      setError("Only .docx and .pdf contract files are supported.");
      return;
    }

    if (selectedFile.size > maxFileSizeBytes) {
      setFile(null);
      setError(`File must be ${maxFileSizeMb} MB or smaller.`);
      return;
    }

    setFile(selectedFile);
  }

  function decidePreflightQuality(checkIndex: number, decision: "keep" | "reverted" | "acknowledged") {
    const fix = preflightAutoFixes.find((item) => item.checkIndex === checkIndex);
    let nextText = editorText;

    if (decision === "reverted" && fix) {
      const replacementIndex = nextText.indexOf(fix.replacement);
      if (replacementIndex >= 0) {
        nextText = (
          nextText.slice(0, replacementIndex)
          + fix.original
          + nextText.slice(replacementIndex + fix.replacement.length)
        );
        syncingEditorRef.current = true;
        editor?.commands.setContent(textToEditorHtml(nextText));
        syncingEditorRef.current = false;
        setEditorText(nextText);
        setModifications((current) => current.filter((item) => item.preflight_check_index !== checkIndex));
      }
    }

    setPreflightQualityDecisions((current) => ({ ...current, [checkIndex]: decision }));
    setPreflightBaseText(nextText);
    setEditorNotice(
      decision === "keep"
        ? "已确认保留系统自动修正。"
        : decision === "reverted"
          ? "已撤销本处自动修正，并恢复原文。"
          : "已确认该提示，无需自动修改。"
    );
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    handleFileSelection(selectedFile);
  }

  async function handleSubmit(event?: FormEvent) {
    if (event) {
      event.preventDefault();
    }

    if (!file) {
      setError("请先选择一份 .docx 或 .pdf 合同。");
      return;
    }

    setIsLoading(true);
    setError(null);
    setReview(null);
    resetEditorState();

    try {
      const result = await reviewContract(file, ["基础质量与合同框架"]);
      const { correctedText, modifications: autoFixes, fixes } = applyAutomaticPreflightFixes(
        result.contract_text ?? "",
        result.preflight_checks ?? [],
      );
      const preflightResult = { ...result, contract_text: correctedText };
      setReview(preflightResult);
      setModifications(autoFixes);
      setPreflightAutoFixes(fixes);
      setPreflightBaseText(correctedText);
      setReviewStage("preflight");
      setEditorNotice(
        autoFixes.length
          ? `已自动修正 ${autoFixes.length} 处明确的标点或文字问题，正在进入下一步。`
          : "基础质量检查完成。"
      );
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
      const paragraph = element?.closest("p");

      clearEditorHighlight();
      if (paragraph instanceof HTMLElement) {
        paragraph.classList.add("contract-paragraph-highlight");
        highlightedParagraphRef.current = paragraph;
        paragraph.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }

      element?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function locateRiskInEditor(risk: ReviewRisk) {
    const candidate = isMissingClause(risk.original_text) ? getInsertionAnchor(risk) ?? "" : risk.original_text;
    if (!candidate) {
      return;
    }

    const match = findFuzzyMatch(editorText, candidate, isMissingClause(risk.original_text) ? 0.72 : 0.82);
    if (!match) {
      return;
    }

    revealEditorSelection(match.from + 1, match.to + 1);
  }

  function focusRisk(risk: ReviewRisk, riskKey: string) {
    setActiveRiskKey(riskKey);
    locateRiskInEditor(risk);
    riskCardRefs.current[riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function applyMissingSuggestion(risk: ReviewRisk, riskKey: string, anchorText: string | null) {
    if (!editor) {
      setError("编辑器尚未准备好，请稍后重试。");
      return;
    }

    const currentText = editorText;
    const currentHtml = editor.getHTML();
    const htmlParagraphs = getHtmlParagraphs(currentHtml);
    const anchor = anchorText ?? "";
    const fuzzyAnchorMatch = anchor ? findFuzzyMatch(currentText, anchor, 0.72) : null;
    const anchorMeta = fuzzyAnchorMatch ? getParagraphMetaFromOffset(currentText, fuzzyAnchorMatch.from) : null;

    const nextParagraphs = textToParagraphs(currentText);
    const insertAtIndex = anchorMeta ? anchorMeta.index + 1 : nextParagraphs.length;
    nextParagraphs.splice(insertAtIndex, 0, risk.suggestion);

    const nextHtmlParagraphs = [...htmlParagraphs];
    nextHtmlParagraphs.splice(insertAtIndex, 0, buildInsertedParagraphHtml(risk.suggestion));

    editor.commands.setContent(nextHtmlParagraphs.join(""));
    setEditorText(nextParagraphs.join("\n"));
    setError(null);
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
    clearInsertionHighlight();
    setEditorNotice(
      anchorMeta ? `已在指定段落后追加“${risk.item}”的补充条款。` : `已追加“${risk.item}”的补充条款到合同末尾。`
    );
    setModifications((previous) => [
      ...previous.filter((item) => item.modified !== risk.suggestion),
      {
        item: risk.item,
        original: MISSING_SENTINEL,
        modified: risk.suggestion,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: anchorText ?? risk.insert_after_text ?? risk.anchor_text ?? null
      }
    ]);
    void submitFeedback(risk, riskKey, "edited", risk.suggestion);

    const insertedOffset = nextParagraphs.slice(0, insertAtIndex).join("\n").length + (insertAtIndex > 0 ? 1 : 0);
    revealEditorSelection(Math.max(1, insertedOffset + 1), Math.max(1, insertedOffset + risk.suggestion.length + 1));
  }

  function applySuggestion(risk: ReviewRisk, riskKey: string) {
    if (!editor) {
      setError("编辑器尚未准备好，请稍后重试。");
      return;
    }

    const missing = isMissingClause(risk.original_text);
    const currentText = editorText;
    setActiveRiskKey(riskKey);

    if (missing) {
      const anchor = getInsertionAnchor(risk) ?? "";
      const anchorMatch = anchor ? findFuzzyMatch(currentText, anchor, 0.72) : null;

      if (anchorMatch) {
        applyMissingSuggestion(risk, riskKey, anchorMatch.matchedText);
        return;
      }

      setManualInsertRiskKey(riskKey);
      setManualInsertAfterText(paragraphOptions[0]?.anchor ?? "");
      clearInsertionHighlight();
      setEditorNotice(`“${risk.item}” 暂未锁定插入位置，请选择要插入到哪一段后面。`);
      setError(null);
      return;
    }

    const originalMatch = findFuzzyMatch(currentText, risk.original_text, 0.84);
    if (!originalMatch) {
      setError("未在当前合同正文中找到对应原文，可能已被修改或模型返回的原文不完全一致。");
      return;
    }

    const paragraphMeta = getParagraphMetaFromOffset(currentText, originalMatch.from);
    if (!paragraphMeta) {
      setError("未能定位对应段落，请稍后重试。");
      return;
    }

    const nextText =
      currentText.slice(0, originalMatch.from) + risk.suggestion + currentText.slice(originalMatch.to);
    const currentHtml = editor.getHTML();
    const htmlParagraphs = getHtmlParagraphs(currentHtml);
    const nextHtmlParagraphs = [...htmlParagraphs];
    nextHtmlParagraphs[paragraphMeta.index] = buildReplacementDiffHtml(
      paragraphMeta.text,
      risk.original_text,
      risk.suggestion
    );

    editor.commands.setContent(nextHtmlParagraphs.join(""));
    setEditorText(nextText);
    setError(null);
    setEditorNotice(`已引用“${risk.item}”的修改建议。`);
    setModifications((previous) => [
      ...previous.filter((item) => item.original !== risk.original_text),
      {
        item: risk.item,
        original: risk.original_text,
        modified: risk.suggestion,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: risk.insert_after_text ?? null
      }
    ]);
    void submitFeedback(risk, riskKey, "edited", risk.suggestion);

    revealEditorSelection(Math.max(1, originalMatch.from + 1), Math.max(1, originalMatch.from + risk.suggestion.length + 1));
  }

  function continueToDeepReview(preflightResult: ReviewResponse, contractText: string) {
    const unresolvedFramework = (preflightResult.preflight_checks ?? []).filter(
      (check) => (check.category === "structure" || check.category === "scope") && check.status === "warning"
    );
    const retainedPreflightWarning = unresolvedFramework.length
      ? `第一轮框架检查有 ${unresolvedFramework.length} 项由您选择暂不补充；深度审查不会重复展示这些项目。`
      : null;
    setReview({
      ...preflightResult,
      contract_text: contractText,
      warnings: retainedPreflightWarning
        ? [...new Set([...preflightResult.warnings, retainedPreflightWarning])]
        : preflightResult.warnings,
    });
    setReviewStage("deep_ready");
    setEditorNotice("第一轮框架与文字检查已确认。下一步请设置我方身份和业务目标，开始第二轮深度利益审查。");
  }

  function toggleDeepSettingOption(field: "focus_areas" | "special_requirements", option: string) {
    setDeepReviewSettings((current) => {
      const selected = current[field];
      const next = selected.includes(option)
        ? selected.filter((item) => item !== option)
        : [...selected, option];
      return { ...current, [field]: next };
    });
  }

  async function runDeepReview() {
    if (!review) return;
    if (!deepReviewSettings.party_role) {
      setError("请先选择我方在合同中的身份；深度审查不能默认合同立场。");
      return;
    }
    if (deepReviewSettings.party_role === "other" && !deepReviewSettings.other_party_role.trim()) {
      setError("请选择“其他”身份后，请说明我方在本合同中的角色。");
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const result = await reviewContractDeeply(
        review.filename,
        editorText,
        deepReviewSettings as DeepReviewSettings,
      );
      if (!result.deep_review || result.deep_review.state !== "completed" || !result.deep_review.executive_summary.trim()) {
        throw new Error("深度审查未返回完整的审查说明，系统未开放修改与导出。");
      }

      setReview({
        ...result,
        contract_text: editorText,
        preflight_checks: [],
        manual_review_required: true,
      });
      setReviewStage("modification");
      setEditorNotice("深度审查已完成。请按“必须修改 / 可谈判 / 内部审批”处理建议，再导出无修订痕迹的最终合同。");
    } catch (reviewError) {
      setError(getErrorMessage(reviewError));
      setEditorNotice("深度审查未形成可验证结果，正文仍保持锁定；请检查模型服务后重试。");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitFeedback(
    risk: ReviewRisk,
    riskKey: string,
    decision: FeedbackDecision,
    correctedSuggestion?: string
  ) {
    if (!review) return;
    setRiskFeedback((previous) => ({ ...previous, [riskKey]: decision }));
    try {
      await recordReviewFeedback(review.filename, risk.item, decision, correctedSuggestion);
    } catch (feedbackError) {
      setRiskFeedback((previous) => {
        const next = { ...previous };
        delete next[riskKey];
        return next;
      });
      setError(feedbackError instanceof Error ? feedbackError.message : "复核反馈记录失败。");
    }
  }

  async function handleExport() {
    if (file && !file.name.toLowerCase().endsWith(".docx")) {
      setError("PDF can be reviewed, but Word tracked-change export is not supported.");
      return;
    }

    if (!file) {
      setError("请先选择一份 .docx 合同。");
      return;
    }

    const editorModifications = review?.contract_text && editorText !== review.contract_text
      ? buildEditorModifications(review.contract_text, editorText)
      : [];
    const exportModifications = [
      ...modifications,
      ...editorModifications.filter((editorModification) => !modifications.some((existing) => (
        existing.original === editorModification.original
        && existing.modified === editorModification.modified
      ))),
    ];

    if (!exportModifications.length) {
      setError("请先在右侧风险卡片中引用或追加至少一条修改。");
      return;
    }

    setIsExporting(true);
    setError(null);

    try {
      const blob = await exportReviewedContract(file, exportModifications);
      downloadBlob(blob, "final_contract.docx");
      setEditorNotice("最终版合同已生成并开始下载。");
    } catch (exportError) {
      setError(getErrorMessage(exportError));
    } finally {
      setIsExporting(false);
    }
  }

  const currentFilename = review?.filename ?? file?.name ?? "未选择合同";
  const currentFileSize = file ? formatFileSize(file.size) : null;

  return (
    <main className="app-shell">
      <header className="topbar" aria-label="应用状态">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">
            LA
          </span>
          <div>
            <strong>Legal AI</strong>
            <span>企业合同审阅工作台</span>
          </div>
        </div>
        <div className="system-status-container">
          <button
            className="system-status-btn"
            type="button"
            onClick={() => {
              setIsSystemStatusOpen((prev) => !prev);
              setSystemStatusError(null);
            }}
            aria-expanded={isSystemStatusOpen}
          >
            <span className="status-indicator-dot"></span>
            系统状态
          </button>
          {isSystemStatusOpen && (
            <div className="system-status-dropdown">
              {systemStatusError ? <p className="system-status-error">{systemStatusError}</p> : null}
              {systemStatus ? <>
                <div className="dropdown-item">
                  <span className="dropdown-label">知识库</span>
                  <span className="dropdown-value">{systemStatus.knowledge_base.collection ?? "未配置"} · {systemStatus.knowledge_base.host ?? "未配置"}</span>
                </div>
                <div className="dropdown-item">
                  <span className="dropdown-label">审查模型</span>
                  <span className="dropdown-value">{systemStatus.review_model.model ?? "未配置"} · {systemStatus.review_model.configured ? "已配置" : "待配置"}</span>
                </div>
                <div className="dropdown-item">
                  <span className="dropdown-label">PDF 解析</span>
                  <span className="dropdown-value">{systemStatus.pdf_parser.host ?? "未配置"}</span>
                </div>
                <div className="dropdown-item">
                  <span className="dropdown-label">重排序</span>
                  <span className="dropdown-value">{systemStatus.reranker.enabled ? systemStatus.reranker.host ?? "待配置" : "已关闭"}</span>
                </div>
              </> : !systemStatusError ? <p className="system-status-loading">正在读取真实服务配置…</p> : null}
              <div className="dropdown-item">
                <span className="dropdown-label">在线编辑</span>
                <span className="dropdown-value">Tiptap 审阅草稿</span>
              </div>
            </div>
          )}
        </div>
      </header>

      <input
        ref={fileInputRef}
        className="hidden-file-input"
        type="file"
        accept=".docx,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf"
        onChange={handleFileChange}
      />

      {!review ? (
        <div className="upload-container">
          <div className="upload-header">
            <div className="workflow-steps" aria-label="审核流程">
              <span className="workflow-step workflow-step-active"><b>1</b>上传合同</span>
              <i aria-hidden="true" />
              <span className="workflow-step"><b>2</b>选择范围</span>
              <i aria-hidden="true" />
              <span className="workflow-step"><b>3</b>查看建议</span>
            </div>
            <h1>上传合同，生成可追溯法规依据的审查建议。</h1>
            <p>审查完成后，可在原文中定位风险条款并查看修改建议。</p>
          </div>

          <div className="upload-card">
            <div
              className={`upload-dropzone ${file ? "upload-dropzone-active" : ""}`}
              onClick={() => !isLoading && fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (isLoading) return;
                const droppedFile = e.dataTransfer.files?.[0];
                if (droppedFile) {
                  handleFileSelection(droppedFile);
                }
              }}
            >
              <div className="upload-dropzone-inner">
                {file ? (
                  <>
                    <span className="upload-file-icon">📄</span>
                    <strong className="upload-file-name">{file.name}</strong>
                    <span className="upload-file-size">{formatFileSize(file.size)}</span>
                  </>
                ) : (
                  <>
                    <span className="upload-icon">📥</span>
                    <strong>拖入或选择合同文件</strong>
                    <span className="upload-hint">支持 .docx / .pdf 格式，最大 10MB；DOCX 审查后可在线引用修改</span>
                  </>
                )}
              </div>
            </div>

            <section className="fixed-review-flow" aria-label="固定审查流程">
              <strong>系统将自动完成初步全量审查</strong>
              <span>先进行基础质量与合同框架检查，再覆盖全部初步审查项目；初审结束后进入深度审查，深度审查完成才可修改与导出。</span>
            </section>

            <div className="upload-action-row">
              {!file ? (
                <button
                  className="primary-button upload-submit-btn"
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                >
                  选择合同并开始审查
                </button>
              ) : (
                <div className="upload-btn-group">
                  <button
                    className="primary-button upload-submit-btn"
                    type="button"
                    disabled={isLoading}
                    onClick={() => void handleSubmit()}
                  >
                    {isLoading ? "正在执行初步审查…" : "上传并开始初步审查"}
                  </button>
                  {!isLoading && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => {
                        setFile(null);
                        setError(null);
                        if (fileInputRef.current) {
                          fileInputRef.current.value = "";
                        }
                      }}
                    >
                      重新选择
                    </button>
                  )}
                </div>
              )}
            </div>

            {isLoading && (
              <div className="upload-progress-panel">
                <div className="progress-bar" />
                <p>正在解析合同、检索法规并生成审查意见…</p>
              </div>
            )}

            {error && <p className="error-message upload-error">{error}</p>}

            <div className="upload-status-footer">
              <span className="status-dot"></span>
              法规知识库已连接 · 审查模型 Qwen-Max
            </div>
          </div>
        </div>
      ) : (
        <section className={`workspace${isSidebarCollapsed ? " workspace-collapsed" : ""}`} aria-busy={isLoading}>
          <section className="reader-panel">
            <div className="compact-document-bar">
              <div className="document-info">
                <span className="document-icon" aria-hidden="true">
                  📄
                </span>
                <div>
                  <strong>{currentFilename}</strong>
                  <span className="document-size">
                    {currentFileSize ?? "文件已载入"} · {review.contract_type ?? "通用商务合同"}
                  </span>
                </div>
              </div>
              <div className="compact-document-actions">
                {isSidebarCollapsed && (
                  <button
                    className="secondary-button compact-expand-btn"
                    type="button"
                    onClick={() => setIsSidebarCollapsed(false)}
                  >
                    展开结果
                  </button>
                )}
                <button className="compact-reupload-btn" type="button" onClick={() => fileInputRef.current?.click()}>
                  重新上传
                </button>
                <button className="secondary-button compact-clear-btn" type="button" onClick={clearReview}>
                  清空
                </button>
                <button className="primary-button compact-review-btn" type="button" disabled={!canSubmit} onClick={(event) => void handleSubmit(event as never)}>
                  {isLoading ? "正在生成审查结果…" : "重新审查"}
                </button>
              </div>
            </div>

            {isLoading ? (
              <div className="process-panel" role="status" aria-live="polite">
                <div className="progress-bar" />
                <p>正在解析合同、检索法规并生成审查意见…</p>
              </div>
            ) : null}

            {error ? <p className="error-message">{error}</p> : null}
            {editorNotice ? <p className="success-message">{editorNotice}</p> : null}

            <section className="editor-panel editor-panel-promoted" aria-label="合同正文编辑">
              <div className="editor-heading">
                <div>
                  <p className="editor-edit-hint">可直接点击正文进行编辑；右侧“定位”按钮会跳转到对应条款。</p>
                  <h2>合同正文</h2>
                </div>
                <span>{editorText ? `${editorText.length} 字` : "未载入"}</span>
              </div>

              {manualInsertRiskKey ? (
                <div className="editor-mode-banner" role="status" aria-live="polite">
                  正在手动选择插入位置：点击正文中的目标段落，补充条款会插入到该段后面。
                </div>
              ) : null}

              {reviewStage !== "modification" ? (
                <div className="modification-locked-banner" role="status">
                  {reviewStage === "deep_ready"
                    ? "初步审查已完成，等待深度审查提示词接入。深度审查完成后将开放正文修改和最终导出。"
                    : "正在完成前置审查流程，正文修改与最终导出将在深度审查完成后开放。"}
                </div>
              ) : null}

              <div className="editor-toolbar" role="toolbar" aria-label="正文格式工具">
                <button type="button" title="加粗" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleBold().run()}><strong>B</strong></button>
                <button type="button" title="斜体" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleItalic().run()}><em>I</em></button>
                <button type="button" title="下划线" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleUnderline().run()}><u>U</u></button>
                <button type="button" className="highlight-tool" title="黄色高亮" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleHighlight({ color: "#fff19a" }).run()}>A</button>
                <button type="button" className="text-color-tool" title="绿色文字" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().setColor("#146b49").run()}>A</button>
                <button type="button" title="清除文字格式" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().unsetAllMarks().run()}>清除格式</button>
                <span className="toolbar-divider" aria-hidden="true" />
                <button type="button" title="撤销" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().undo().run()}>↶</button>
                <button type="button" title="重做" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().redo().run()}>↷</button>
              </div>

              <div className={`editor-page editor-page-promoted${isSidebarCollapsed ? " editor-page-focus" : ""}`}>
                <EditorContent editor={editor} />
              </div>

              <div className="export-row">
                <div>
                  <strong>{modifications.length}</strong>
                  <span>条已接受修改</span>
                </div>
                <button className="primary-button" type="button" disabled={reviewStage !== "modification" || !canExport} onClick={() => void handleExport()}>
                  {isExporting ? "导出中" : "确认并导出最终版"}
                </button>
              </div>
            </section>
          </section>

          <aside className={`review-sidebar${isSidebarCollapsed ? " review-sidebar-collapsed" : ""}`}>
            <section className="result-panel">
              <div className="result-header">
                <div className="result-header-title-row">
                  <div>
                    <h2>审查结果</h2>
                    {review.contract_type ? <p className="result-subtitle">合同类型：{review.contract_type}</p> : null}
                  </div>
                  <button className="secondary-button compact-review-btn" type="button" onClick={() => void handleReportExport()} disabled={isReportExporting}>
                    {isReportExporting ? "导出中…" : "导出审查报告"}
                  </button>
                  <button
                    className="sidebar-collapse-btn"
                    type="button"
                    onClick={() => setIsSidebarCollapsed(true)}
                    title="收起结果"
                  >
                    收起结果
                  </button>
                </div>
                <div className="score-summary" aria-label="风险统计">
                  <span className="score-high">高风险 {riskCounts.high}</span>
                  <span className="score-medium">中风险 {riskCounts.medium}</span>
                  <span className="score-low">低风险 {riskCounts.low}</span>
                </div>
              </div>

              {isLoading ? (
                <div className="loading-stack">
                  <div className="skeleton-line skeleton-title" />
                  <div className="skeleton-card" />
                  <div className="skeleton-card skeleton-card-short" />
                </div>
              ) : null}

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

                <div className="review-progress-panel">
                  <div className="review-progress-heading">
                    <div>
                      <strong>审核进度</strong>
                      <span>{reviewProgress.checked}/{reviewProgress.total} 个范围已完成规则检查</span>
                    </div>
                    <b>{reviewProgress.percentage}%</b>
                  </div>
                  <div className="review-progress-track"><span style={{ width: `${reviewProgress.percentage}%` }} /></div>
                  <div className="review-progress-meta">
                    <span>法规依据已核验 {reviewProgress.verified} 项</span>
                    <span>风险已处理 {processedRiskCount}/{totalRisks} 项</span>
                  </div>
                </div>

                {reviewStage === "preflight" && review.review_scope.includes("基础质量与合同框架") ? (
                  <section className="preflight-panel" aria-label="基础质量与合同框架检查">
                    <div className="preflight-heading">
                      <div>
                        <strong>第一步：基础质量与合同框架</strong>
                        <span>{reviewStage === "preflight" ? "正在处理基础预检；完成后将进入条款利益审查" : "本地规则预检，不计入法律风险"}</span>
                      </div>
                      <b className={preflightWarnings.length ? "preflight-count-warning" : "preflight-count-passed"}>
                        {preflightWarnings.length ? `待确认 ${preflightWarnings.length} 项` : "检查通过"}
                      </b>
                    </div>
                    <p className="preflight-description">
                      先核对标题、主体、条款层级、签署区，以及可客观定位的重复标点、异常字符和疑似重复输入；错别字候选均需人工确认。
                    </p>
                    <div className="preflight-list">
                      {preflightChecks.map((check, index) => (
                        <article className={`preflight-row preflight-row-${check.status}`} key={`${check.category}-${check.title}-${index}`}>
                          <div className="preflight-row-heading">
                            <span className={`preflight-category preflight-category-${check.category}`}>
                              {check.category === "structure" ? "框架" : check.category === "scope" ? "范围" : check.category === "punctuation" ? "标点" : "文字"}
                            </span>
                            <strong>{check.title}</strong>
                            <b>{check.status === "passed" ? "已检查" : preflightQualityDecisions[index] ? "已确认" : "待确认"}</b>
                          </div>
                          {check.evidence ? <p>{check.evidence}</p> : null}
                          {check.suggestion ? <small>建议：{check.suggestion}</small> : null}
                          {check.auto_fixable && check.status === "warning" ? (
                            <div className="preflight-quality-actions">
                              <small className="preflight-auto-fixed">已自动写入正文，请确认保留或撤销本处。</small>
                              <div>
                                <button type="button" className={preflightQualityDecisions[index] === "keep" ? "preflight-quality-active" : ""} onClick={() => decidePreflightQuality(index, "keep")}>确认保留</button>
                                <button type="button" className={preflightQualityDecisions[index] === "reverted" ? "preflight-quality-active" : ""} onClick={() => decidePreflightQuality(index, "reverted")}>撤销本处</button>
                              </div>
                            </div>
                          ) : check.status === "warning" && check.category !== "structure" && check.category !== "scope" ? (
                            <div className="preflight-quality-actions">
                              <small>请核对原文后确认是否保留。</small>
                              <div><button type="button" className={preflightQualityDecisions[index] === "acknowledged" ? "preflight-quality-active" : ""} onClick={() => decidePreflightQuality(index, "acknowledged")}>已核对</button></div>
                            </div>
                          ) : null}
                        </article>
                      ))}
                    </div>
                    {reviewStage === "preflight" ? (
                      <div className="framework-decision-panel">
                        {frameworkPreflightChecks.length ? (
                          <>
                            <strong>框架缺失需由您决定</strong>
                            <p>系统不会擅自新增主体、标题、条款层级或签署区内容。选择“我来补充”后，请在左侧正文完成编辑。</p>
                        {frameworkPreflightChecks.map((check, index) => (
                          <div className="framework-decision-row" key={`${check.title}-${index}`}>
                            <div>
                              <b>{check.title}</b>
                              <span>{check.evidence}</span>
                            </div>
                            <div>
                              <button
                                type="button"
                                className={preflightDecisions[index] === "add" ? "framework-choice-active" : ""}
                                onClick={() => {
                                  setPreflightDecisions((current) => ({ ...current, [index]: "add" }));
                                  setEditorNotice(`请在左侧正文中补充“${check.title}”后，再继续下一步。`);
                                }}
                              >
                                我来补充
                              </button>
                              <button
                                type="button"
                                className={preflightDecisions[index] === "skip" ? "framework-choice-active" : ""}
                                onClick={() => setPreflightDecisions((current) => ({ ...current, [index]: "skip" }))}
                              >
                                暂不补充
                              </button>
                            </div>
                          </div>
                        ))}
                          </>
                        ) : (
                          <>
                            <strong>第一轮检查已完成</strong>
                            <p>合同框架与文字标点检查未发现需要您确认的项目，可以进入第二轮深度审查。</p>
                          </>
                        )}
                        <button
                          className="primary-button preflight-continue-button"
                          type="button"
                          disabled={!canAdvancePreflight || isLoading}
                          onClick={() => continueToDeepReview(review, editorText)}
                        >
                          {isLoading ? "正在进入下一步…" : frameworkAddSelected && editorText.trim() === preflightBaseText.trim() ? "请先在正文完成补充" : "确认并进入第二轮深度审查"}
                        </button>
                      </div>
                    ) : null}
                  </section>
                ) : null}

                {reviewStage === "deep_ready" ? (
                  <section className="deep-review-gate deep-review-gate-legacy" aria-label="深度审查准备">
                    <div>
                      <strong>下一步：深度审查</strong>
                      <span>初步全量审查已完成；深度审查将按你下一步提供的提示词，对利益倾向、谈判空间与业务场景开展强化分析。</span>
                    </div>
                    <b>等待深度审查规则</b>
                  </section>
                ) : null}

                {reviewStage === "deep_ready" ? (
                  <section className="deep-review-settings" aria-label="深度审查设置">
                    <div className="deep-review-heading">
                      <div><strong>深度审查：确认我方立场</strong><span>必填项决定审查的利益倾向；未选择不会调用模型，也不会开放修改。</span></div>
                      <b>模型审查 · 人工复核</b>
                    </div>
                    <fieldset className="deep-fieldset">
                      <legend>我方在合同中的身份 <em>必填</em></legend>
                      <div className="deep-option-grid role-options">
                        {([ ["party_a", "甲方 / 采购方 / 客户 / 被许可方"], ["party_b", "乙方 / 供应商 / 服务方 / 许可方"], ["other", "其他角色"] ] as const).map(([role, label]) => (
                          <label className={deepReviewSettings.party_role === role ? "deep-option-selected" : ""} key={role}><input type="radio" name="party-role" checked={deepReviewSettings.party_role === role} onChange={() => setDeepReviewSettings((current) => ({ ...current, party_role: role }))} />{label}</label>
                        ))}
                      </div>
                      {deepReviewSettings.party_role === "other" ? <input className="deep-text-input" value={deepReviewSettings.other_party_role} onChange={(event) => setDeepReviewSettings((current) => ({ ...current, other_party_role: event.target.value }))} placeholder="例如：合作开发方、受托处理方" /> : null}
                    </fieldset>
                    <fieldset className="deep-fieldset">
                      <legend>本次特别关注 <small>可选；不选则全面审查</small></legend>
                      <div className="deep-chip-list">{deepFocusOptions.map((option) => <label className={deepReviewSettings.focus_areas.includes(option) ? "deep-chip-selected" : ""} key={option}><input type="checkbox" checked={deepReviewSettings.focus_areas.includes(option)} onChange={() => toggleDeepSettingOption("focus_areas", option)} />{option}</label>)}</div>
                    </fieldset>
                    <div className="deep-select-row">
                      <label>审查强度<select value={deepReviewSettings.review_style} onChange={(event) => setDeepReviewSettings((current) => ({ ...current, review_style: event.target.value as ReviewStyle }))}><option value="protective">严格保护我方利益</option><option value="balanced">平衡商业合作</option><option value="material_only">仅提示重大问题</option></select></label>
                      <label>合同类型（可选）<input value={deepReviewSettings.contract_type} onChange={(event) => setDeepReviewSettings((current) => ({ ...current, contract_type: event.target.value }))} placeholder="例如：SaaS 服务合同" /></label>
                    </div>
                    <fieldset className="deep-fieldset">
                      <legend>我方不可让步事项 <small>可选</small></legend>
                      <div className="deep-chip-list">{deepRequirementOptions.map((option) => <label className={deepReviewSettings.special_requirements.includes(option) ? "deep-chip-selected" : ""} key={option}><input type="checkbox" checked={deepReviewSettings.special_requirements.includes(option)} onChange={() => toggleDeepSettingOption("special_requirements", option)} />{option}</label>)}</div>
                    </fieldset>
                    <label className="deep-textarea-label">业务背景、交易目标或特殊要求（可选）<textarea value={deepReviewSettings.business_context} maxLength={2000} onChange={(event) => setDeepReviewSettings((current) => ({ ...current, business_context: event.target.value }))} placeholder="例如：上线节点、预算边界、数据类型、拟合作期限" /></label>
                    <label className="deep-textarea-label">不可接受的底线（可选）<textarea value={deepReviewSettings.non_negotiables} maxLength={2000} onChange={(event) => setDeepReviewSettings((current) => ({ ...current, non_negotiables: event.target.value }))} placeholder="例如：不得预付超过 30%，不得将数据用于模型训练" /></label>
                    <button className="primary-button deep-review-start" type="button" disabled={isLoading || !deepReviewSettings.party_role} onClick={() => void runDeepReview()}>{isLoading ? "正在进行深度审查…" : "开始甲方法务深度审查"}</button>
                  </section>
                ) : null}

                {reviewStage === "modification" && review.deep_review ? (
                  <section className="deep-review-result" aria-label="深度审查结论">
                    <div className="deep-review-heading"><div><strong>深度审查结论：{review.deep_review.overall_conclusion}</strong><span>{review.deep_review.settings_note}</span></div><b>已完成</b></div>
                    <p>{review.deep_review.executive_summary}</p>
                    {review.deep_review.key_facts.length ? <details open><summary>关键条款与结论</summary><div className="deep-result-list">{review.deep_review.key_facts.map((fact, index) => <article key={`${fact.item}-${index}`}><b>{fact.item}</b><span>{fact.contract_term}</span><small>{fact.conclusion}</small></article>)}</div></details> : null}
                    {review.deep_review.missing_clauses.length ? <details><summary>需补充的条款</summary><ul>{review.deep_review.missing_clauses.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
                    {review.deep_review.negotiation_items.length ? <details><summary>谈判清单</summary><div className="deep-result-list">{review.deep_review.negotiation_items.map((item, index) => <article key={`${item.topic}-${index}`}><b>{item.topic} · {item.owner}</b><span>目标：{item.target}</span><small>底线：{item.minimum_acceptable}</small></article>)}</div></details> : null}
                    {review.deep_review.clarification_questions.length ? <details><summary>待业务确认</summary><ul>{review.deep_review.clarification_questions.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
                  </section>
                ) : null}

                <div className={`review-integrity-panel review-integrity-${review.review_status}`}>
                  <div className="review-integrity-heading">
                    <strong>
                      {review.review_status === "complete"
                        ? "审查已完成"
                        : review.review_status === "partial"
                          ? "审查部分完成"
                          : "需要人工复核"}
                    </strong>
                    {review.review_duration_ms ? <span>{review.review_duration_ms} ms</span> : null}
                  </div>
                  <p>{review.review_summary || "系统未返回可验证的审查说明。"}</p>
                  {review.warnings.length ? (
                    <ul>
                      {review.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                    </ul>
                  ) : null}
                </div>

                {review.document_quality ? (
                  <div className={`document-quality-panel document-quality-${review.document_quality.status}`}>
                    <div>
                      <strong>文档文本质量</strong>
                      <span>
                        {review.document_quality.status === "searchable"
                          ? "可搜索文本"
                          : review.document_quality.status === "partial"
                            ? "部分识别"
                            : review.document_quality.status === "scanned"
                              ? "疑似扫描件"
                              : "DOCX 原生文本"}
                      </span>
                    </div>
                    <p>{review.document_quality.note}</p>
                    {review.document_quality.kind === "pdf" ? (
                      <small>
                        {review.document_quality.pages ?? 0} 页 · 已提取 {review.document_quality.extracted_chars} 个字符
                        {review.document_quality.ocr_detected ? " · 检测到 OCR" : ""}
                      </small>
                    ) : null}
                  </div>
                ) : null}

                {(review.consistency_checks?.length || review.policy_version) ? (
                  <div className="consistency-panel" aria-label="合同一致性检查">
                    <div className="consistency-panel-heading">
                      <strong>规范化检查</strong>
                      <span>政策版本 {review.policy_version || "2026.08"}</span>
                    </div>
                    <p className="consistency-method">
                      审核方式：{review.review_method === "model" ? "模型" : review.review_method === "rule" ? "规则" : "模型 + 规则"}
                      {review.manual_review_required !== false ? "；结果需人工复核" : ""}
                    </p>
                    {review.consistency_checks?.length ? (
                      <div className="consistency-list">
                        {review.consistency_checks.map((check) => (
                          <div className={`consistency-row consistency-row-${check.status}`} key={check.check}>
                            <span>{check.check}</span>
                            <strong>{check.status === "warning" ? "需确认" : "已检查"}</strong>
                            <small>{check.note}</small>
                            {check.evidence ? (
                              <details className="consistency-evidence">
                                <summary>查看依据</summary>
                                <p>{check.evidence}</p>
                              </details>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : <small>本次范围内未触发内部一致性检查。</small>}
                  </div>
                ) : null}

                {reviewStage === "preflight" ? (
                  <div className="scope-panel sidebar-scope" aria-label="初步审查范围">
                    <p>第一轮：框架检查范围</p>
                    <div>
                      {(review.review_scope.length ? review.review_scope : reviewScopes).map((scope) => (
                        <span key={scope}>{scope}</span>
                      ))}
                    </div>
                    {review.coverage.length ? (
                      <div className="coverage-list">
                        {review.coverage.map((item) => (
                          <div className="coverage-row" key={item.topic}>
                            <span>{item.topic}</span>
                            <strong className={`coverage-status coverage-status-${item.status}`}>
                              {item.status === "checked" ? "已检查" : item.status === "missing" ? "未检出" : "不确定"}
                            </strong>
                            {item.evidence ? <small title={item.evidence}>{item.evidence}</small> : null}
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <div className="risk-filter-bar" role="tablist" aria-label="风险筛选">
                  <button
                    className={`filter-chip${riskFilter === "all" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("all")}
                  >
                    全部 {sortedRisks.length}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "high" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("high")}
                  >
                    高风险 {riskCounts.high}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "medium" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("medium")}
                  >
                    中风险 {riskCounts.medium}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "low" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("low")}
                  >
                    低风险 {riskCounts.low}
                  </button>
                </div>

                <div className="risk-filter-bar risk-filter-secondary" aria-label="处理状态筛选">
                  <button
                    className={`filter-chip${riskFilter === "pending" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("pending")}
                  >
                    待处理 {totalRisks - processedRiskCount}
                  </button>
                  <button
                    className={`filter-chip${riskFilter === "processed" ? " filter-chip-active" : ""}`}
                    type="button"
                    onClick={() => setRiskFilter("processed")}
                  >
                    已处理 {processedRiskCount}
                  </button>
                </div>

                <div className="risk-list">
                  {filteredRisks.length ? (
                    filteredRisks.map(({ risk, riskKey }) => {
                      const showManualInsert = manualInsertRiskKey === riskKey && isMissingClause(risk.original_text);
                      const accepted = modifications.some((item) => (
                        item.item === risk.item
                        && (
                          item.original === risk.original_text
                          || (isMissingClause(risk.original_text) && item.modified === risk.suggestion)
                        )
                      ));
                      const feedbackDecision = riskFeedback[riskKey];

                      return (
                        <article
                          ref={(element) => {
                            riskCardRefs.current[riskKey] = element;
                          }}
                          className={`risk-card risk-card-${risk.level}${activeRiskKey === riskKey ? " risk-card-active" : ""}`}
                          key={riskKey}
                        >
                          <div className="risk-card-header">
                            <div>
                              <div className="risk-chip-row">
                                <span>{levelLabel[risk.level]}</span>
                                <span className={`acceptance-chip${accepted ? " acceptance-chip-done" : ""}`}>
                                  {accepted ? "已处理" : "待处理"}
                                </span>
                              </div>
                              <span className={`evidence-chip${risk.evidence_status === "verified" ? " evidence-chip-verified" : ""}`}>
                                {risk.evidence_status === "verified" ? "依据已核验" : "需人工核验"}
                              </span>
                              {feedbackDecision ? (
                                <span className={`feedback-chip feedback-chip-${feedbackDecision}`}>
                                  {feedbackDecision === "confirmed" ? "已确认风险" : feedbackDecision === "rejected" ? "已标记非风险" : "已采纳修改"}
                                </span>
                              ) : null}
                              <h3>{risk.item}</h3>
                            </div>
                            <div className="risk-actions">
                              <button className="secondary-button inline-button" type="button" onClick={() => focusRisk(risk, riskKey)}>
                                定位
                              </button>
                              <button
                                className={`quote-button${isMissingClause(risk.original_text) ? " quote-append" : ""}${accepted ? " quote-button-done" : ""}`}
                                type="button"
                                disabled={accepted || reviewStage !== "modification"}
                                onClick={() => applySuggestion(risk, riskKey)}
                              >
                                {accepted ? "已处理" : reviewStage !== "modification" ? "深度审查后可修改" : isMissingClause(risk.original_text) ? "追加条款" : "引用修改"}
                              </button>
                              {!feedbackDecision ? (
                                <>
                                  <button
                                    className="secondary-button inline-button"
                                    type="button"
                                    onClick={() => void submitFeedback(risk, riskKey, "confirmed")}
                                  >
                                    确认风险
                                  </button>
                                  <button
                                    className="secondary-button inline-button"
                                    type="button"
                                    onClick={() => void submitFeedback(risk, riskKey, "rejected")}
                                  >
                                    标记非风险
                                  </button>
                                </>
                              ) : null}
                            </div>
                          </div>

                          <div className={`original-block${isMissingClause(risk.original_text) ? " original-missing" : ""}`}>
                            <p className="risk-title">{isMissingClause(risk.original_text) ? "建议插入位置" : "定位原文"}</p>
                            <p>
                              {isMissingClause(risk.original_text)
                                ? getInsertionAnchor(risk) ?? "合同中缺失该约定，暂未锁定明确插入位置，可手动选择段落。"
                                : risk.original_text}
                            </p>
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
                              <p className="manual-insert-hint">可以直接点击左侧正文中的目标段落，或在下方列表中选择。</p>
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
                                  onClick={() => applyMissingSuggestion(risk, riskKey, manualInsertAfterText || null)}
                                >
                                  插入到该段后
                                </button>
                                <button
                                  className="secondary-button"
                                  type="button"
                                  onClick={() => {
                                    setManualInsertRiskKey(null);
                                    setManualInsertAfterText("");
                                    clearInsertionHighlight();
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
                              {risk.law_references.length ? (
                                <ul className="law-source-list">
                                      {risk.law_references.map((reference) => (
                                        <li key={`${reference.label}-${reference.official_url ?? ""}`}>
                                          {reference.official_url ? (
                                            <a href={reference.official_url} target="_blank" rel="noreferrer">
                                              官方来源 · {reference.authority ?? reference.label}
                                            </a>
                                          ) : <span>来源待核验：{reference.label}</span>}
                                          <small className={`law-status law-status-${reference.effectiveness_status === "effective" && reference.official_url ? "verified" : "pending"}`}>
                                            {reference.effectiveness_status === "effective" && reference.official_url ? "现行有效·已核验" : "效力或来源待核验"}
                                          </small>
                                        </li>
                                  ))}
                                </ul>
                              ) : null}
                            </details>
                          ) : null}
                        </article>
                      );
                    })
                  ) : (
                    <div className="no-risk-state">
                      <p>{sortedRisks.length ? "当前筛选条件下暂无风险项。" : "本次没有形成可直接处理的风险项。"}</p>
                      <span>
                        {sortedRisks.length
                          ? "可以切换回全部结果继续查看。"
                          : review.review_status === "complete"
                            ? "关键审查范围均已覆盖，但仍建议由法务人员进行最终复核。"
                            : "这不代表合同无风险，请根据上方覆盖范围和提示进行人工复核。"}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </section>
          </aside>
        </section>
      )}
    </main>
  );
}
