import { Mark, mergeAttributes } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import { TextStyle } from "@tiptap/extension-text-style";
import Underline from "@tiptap/extension-underline";
import { ChangeEvent, FormEvent, type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import {
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

type ContractOverview = {
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

type ContractOverviewResponse = {
  filename: string;
  contract_text: string;
  overview: ContractOverview;
  document_quality?: DocumentQuality | null;
};

type IntakeChatMessage = {
  role: "assistant" | "user";
  content: string;
  intent?: "intake" | "legal_research";
  quick_replies?: string[];
  suggested_questions?: string[];
};

type IntakeReviewCriteria = {
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

type IntakeChatResponse = {
  assistant_message: string;
  quick_replies: string[];
  suggested_questions: string[];
  criteria: IntakeReviewCriteria;
  ready_for_review: boolean;
  source: "model" | "fallback";
  warning?: string | null;
};

type LegalResearchResponse = {
  assistant_message: string;
  suggested_questions: string[];
  source: "model" | "fallback";
  warning?: string | null;
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

type Modification = {
  item?: string;
  risk_key?: string;
  original: string;
  modified: string;
  revision_id?: string;
  anchor_text?: string | null;
  insert_after_text?: string | null;
  paragraph_context?: string | null;
};

type FeedbackDecision = "confirmed" | "rejected" | "edited";
type PreflightDecision = "confirmed" | "deferred";

type ParagraphOption = {
  anchor: string;
  label: string;
};

type RiskWithKey = {
  risk: ReviewRisk;
  riskKey: string;
};

type RiskLocationCandidate = {
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

type ReviewStage = "upload" | "intake" | "modification";
// Retained while older intake helpers remain available for saved local state;
// the visible workflow is now driven by IntakeChatMessage instead.
type IntakeConversationStep = "role" | "objective" | "focus" | "redlines" | "ready";

type DeepReviewFormSettings = Omit<DeepReviewSettings, "party_role"> & {
  party_role: PartyRole | "";
};

const emptyIntakeCriteria: IntakeReviewCriteria = {
  party_role: null,
  other_party_role: "",
  deal_priorities: [],
  focus_areas: [],
  review_style: "protective",
  business_context: "",
  non_negotiables: "",
  special_requirements: [],
  additional_notes: []
};

function criteriaToDeepReviewSettings(criteria: IntakeReviewCriteria, overview: ContractOverview): DeepReviewFormSettings {
  return {
    party_role: criteria.party_role ?? "",
    other_party_role: criteria.other_party_role,
    transaction_stage: "",
    timeline_urgency: "",
    counterparty_context: "",
    deal_priorities: criteria.deal_priorities,
    focus_areas: criteria.focus_areas,
    review_style: criteria.review_style,
    contract_type: overview.contract_type,
    special_requirements: criteria.special_requirements,
    business_context: criteria.business_context,
    non_negotiables: criteria.non_negotiables,
    additional_notes: criteria.additional_notes
  };
}

const DeleteMark = Mark.create({
  name: "deleted",
  addAttributes() {
    return {
      revisionId: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-revision-id"),
        renderHTML: (attributes) => attributes.revisionId ? { "data-revision-id": attributes.revisionId } : {},
      },
    };
  },
  parseHTML() {
    return [{ tag: "del" }, { tag: "span.del-mark" }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["del", mergeAttributes(HTMLAttributes, { class: "del-mark" }), 0];
  }
});

const InsertMark = Mark.create({
  name: "inserted",
  addAttributes() {
    return {
      revisionId: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-revision-id"),
        renderHTML: (attributes) => attributes.revisionId ? { "data-revision-id": attributes.revisionId } : {},
      },
    };
  },
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
const deepFocusOptions = ["主体与授权", "价格与付款", "交付与验收", "质量与售后", "数据与安全", "知识产权", "保密与宣传", "责任与赔偿", "变更管理", "解除与退出", "争议解决", "合规与许可", "全部"];
const deepRequirementOptions = ["控制预付款", "付款与验收结果挂钩", "保留验收权", "限制责任", "不得单方调价或变更", "不得自动续约", "数据不出境", "数据删除与返还", "禁止 AI 训练", "禁止未经同意转包", "保留审计权", "争议在我方所在地", "保护品牌与宣传权", "源代码/材料可交付"];
const dealPriorityOptions = ["按期上线或拿到可用成果", "预算可控，付款与结果挂钩", "保护数据、知识产权和商业秘密", "降低违约、售后与退出成本", "合规可审计、便于内部审批", "优先促成签约，保留必要保护"];
const transactionStageOptions = ["首次收到对方合同/模板", "双方正在谈判条款", "合作已基本确定，重点控风险", "续约、补充协议或变更协议", "已出现履约争议或对方违约"];
const timelineUrgencyOptions = ["暂无明确签约时间压力", "有明确上线/交付节点", "对方催签，但关键保护不能放弃", "紧急签约，只拦截重大风险"];
const counterpartyContextOptions = ["对方提供合同文本", "我方提供合同文本", "双方共同起草或已多轮修改", "不确定，按对方文本风险审查"];
const contractTypeSuggestions = ["软件/SaaS 服务合同", "系统采购与实施合同", "委托开发合同", "采购合同", "数据处理协议", "咨询/技术服务合同", "销售/供货合同", "保密协议"];
const scenarioPresets = [
  {
    name: "系统采购与实施",
    description: "关注交付、验收、数据和后续服务",
    contractType: "系统采购与实施合同",
    focus: ["价格与付款", "交付与验收", "数据与安全", "知识产权", "责任与赔偿"],
    requirements: ["控制预付款", "付款与验收结果挂钩", "保留验收权", "数据删除与返还", "保留审计权"],
    priorities: ["按期上线或拿到可用成果", "预算可控，付款与结果挂钩", "保护数据、知识产权和商业秘密"],
  },
  {
    name: "委托开发/定制",
    description: "关注成果归属、源代码、变更与验收",
    contractType: "委托开发合同",
    focus: ["交付与验收", "知识产权", "变更管理", "责任与赔偿", "解除与退出"],
    requirements: ["保留验收权", "源代码/材料可交付", "不得单方调价或变更", "限制责任"],
    priorities: ["按期上线或拿到可用成果", "保护数据、知识产权和商业秘密", "降低违约、售后与退出成本"],
  },
  {
    name: "数据处理/系统接入",
    description: "关注数据合规、使用边界和安全责任",
    contractType: "数据处理协议",
    focus: ["数据与安全", "保密与宣传", "合规与许可", "责任与赔偿", "解除与退出"],
    requirements: ["数据不出境", "数据删除与返还", "禁止 AI 训练", "保留审计权", "限制责任"],
    priorities: ["保护数据、知识产权和商业秘密", "合规可审计、便于内部审批", "降低违约、售后与退出成本"],
  },
  {
    name: "采购/咨询服务",
    description: "关注费用、成果质量、人员和退出",
    contractType: "咨询/技术服务合同",
    focus: ["价格与付款", "交付与验收", "质量与售后", "责任与赔偿", "解除与退出"],
    requirements: ["付款与验收结果挂钩", "保留验收权", "不得自动续约", "禁止未经同意转包"],
    priorities: ["预算可控，付款与结果挂钩", "按期上线或拿到可用成果", "降低违约、售后与退出成本"],
  },
] as const;
const emptyEditorHtml = "<p>上传并审查合同后，解析出的正文会显示在这里。</p>";
const placeholderPattern = /【[^】]+】/g;
const unsupportedEditorCharacters = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

function getIntakeRecommendations(overview: ContractOverview) {
  const source = [
    overview.contract_type,
    overview.transaction_subject,
    ...overview.key_terms,
    ...overview.dimensions.flatMap((item) => item.details),
    ...overview.business_flow,
    overview.summary,
  ].join(" ").toLowerCase();
  const focus = new Set<string>();
  const requirements = new Set<string>();

  if (/价|金额|付款|发票|费用|预算|payment|invoice/.test(source)) focus.add("价格与付款");
  if (/交付|验收|上线|实施|服务|交货|delivery|acceptance/.test(source)) focus.add("交付与验收");
  if (/数据|个人信息|隐私|安全|系统|ai|训练|data|privacy/.test(source)) {
    focus.add("数据与安全");
    requirements.add("数据不出境");
    requirements.add("禁止 AI 训练");
  }
  if (/知识产权|著作权|专利|软件|源代码|许可|ip|license/.test(source)) focus.add("知识产权");
  if (/违约|赔偿|责任|免责|保密|liability|indemn/.test(source)) focus.add("责任与赔偿");
  if (/解除|终止|退出|续约|termination|renew/.test(source)) focus.add("解除与退出");
  if (/争议|仲裁|管辖|法院|dispute/.test(source)) focus.add("争议解决");

  if (!focus.size) {
    ["价格与付款", "交付与验收", "责任与赔偿"].forEach((item) => focus.add(item));
  }

  return {
    focus: [...focus],
    requirements: [...requirements],
    rationale: `根据合同概览${focus.size ? `，建议优先核对${[...focus].join("、")}` : "，建议先按通用商业合同标准审查"}。`,
  };
}

function getErrorMessage(error: unknown) {
  if (error instanceof Error) {
    const normalizedMessage = error.message.toLowerCase();

    if (
      normalizedMessage === "failed to fetch"
      || normalizedMessage.includes("networkerror")
      || normalizedMessage.includes("network request failed")
    ) {
      return "无法连接本地后端服务。请确认 http://127.0.0.1:8000/health 可正常访问后重试。";
    }

    if (error.message === "Not Found") {
      return "导出接口暂未在运行中的后端生效，请重启或重建后端服务后再试。";
    }

    if (normalizedMessage.includes("contract overview request failed with status 500")) {
      return "合同概览服务暂不可用。请确认本地后端已启动（http://127.0.0.1:8000/health 应返回正常状态）后重试。";
    }

    if (error.message.includes("DASHSCOPE_API_KEY")) {
      return "百炼 API Key 未配置或未进入容器，请检查 backend/.env 后重启后端服务。";
    }

    if (normalizedMessage.includes("could not be located exactly")) {
      return "Word 审阅版未生成：有修改无法精确定位到原合同。请在右侧点击“定位”，确认对应段落后重新应用该建议。";
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

function buildPreciseParagraphModification(original: string, edited: string): Modification {
  let prefixLength = 0;
  const sharedLength = Math.min(original.length, edited.length);
  while (prefixLength < sharedLength && original[prefixLength] === edited[prefixLength]) {
    prefixLength += 1;
  }

  let suffixLength = 0;
  while (
    suffixLength < original.length - prefixLength
    && suffixLength < edited.length - prefixLength
    && original[original.length - suffixLength - 1] === edited[edited.length - suffixLength - 1]
  ) {
    suffixLength += 1;
  }

  const changedOriginal = original.slice(prefixLength, original.length - suffixLength);
  const changedEdited = edited.slice(prefixLength, edited.length - suffixLength);

  // A pure insertion has no source text that the DOCX exporter can safely
  // anchor as a run-level revision. Keep the existing paragraph-replacement
  // representation for that narrow case. Deletes and replacements retain the
  // smallest exact source span, so Word shows readable local redlines.
  if (!changedOriginal) {
    return { original, modified: edited, paragraph_context: original };
  }
  return {
    original: changedOriginal,
    modified: changedEdited,
    paragraph_context: original,
  };
}

function buildEditorModifications(originalText: string, editedText: string): Modification[] {
  const originalParagraphs = textToParagraphs(originalText);
  const editedParagraphs = textToParagraphs(editedText);
  const modifications: Modification[] = [];
  let originalIndex = 0;
  let editedIndex = 0;

  // Preserve surrounding paragraphs when users insert or remove a paragraph.
  // This avoids turning every paragraph after an insertion into a replacement.
  while (originalIndex < originalParagraphs.length || editedIndex < editedParagraphs.length) {
    const original = originalParagraphs[originalIndex];
    const edited = editedParagraphs[editedIndex];
    if (original === edited) {
      originalIndex += 1;
      editedIndex += 1;
    } else if (original !== undefined && originalParagraphs[originalIndex + 1] === edited) {
      modifications.push({ original, modified: "", paragraph_context: original });
      originalIndex += 1;
    } else if (edited !== undefined && original === editedParagraphs[editedIndex + 1]) {
      modifications.push({
        original: MISSING_SENTINEL,
        modified: edited,
        insert_after_text: originalParagraphs[originalIndex - 1] ?? null,
      });
      editedIndex += 1;
    } else if (original !== undefined && edited !== undefined) {
      modifications.push(buildPreciseParagraphModification(original, edited));
      originalIndex += 1;
      editedIndex += 1;
    } else if (original !== undefined) {
      modifications.push({ original, modified: "", paragraph_context: original });
      originalIndex += 1;
    } else if (edited !== undefined) {
      modifications.push({
        original: MISSING_SENTINEL,
        modified: edited,
        insert_after_text: originalParagraphs[originalIndex - 1] ?? null,
      });
      editedIndex += 1;
    }
  }

  return modifications;
}

function collectExportModifications(applied: Modification[], editorChanges: Modification[]) {
  const supersededApplied = new Set<number>();
  const additional: Modification[] = [];

  for (const editorChange of editorChanges) {
    const overlapping = applied
      .map((change, index) => ({ change, index }))
      .filter(({ change }) => (
        !isMissingClause(change.original)
        && !isMissingClause(editorChange.original)
        && editorChange.original.includes(change.original)
      ));

    if (!overlapping.length) {
      additional.push(editorChange);
      continue;
    }

    // The editor already contains automatic changes. Do not submit a second
    // whole-paragraph replacement for the same content: it would overlap the
    // granular tracked revision in the Word exporter.
    let expectedText = editorChange.original;
    for (const { change } of overlapping) {
      expectedText = expectedText.replace(change.original, change.modified);
    }
    if (expectedText === editorChange.modified) continue;

    // A user also edited that paragraph after the automatic revision. Export
    // its final paragraph once, rather than losing the user's manual edit.
    overlapping.forEach(({ index }) => supersededApplied.add(index));
    additional.push(editorChange);
  }

  const result = [
    ...applied.filter((_change, index) => !supersededApplied.has(index)),
    ...additional,
  ];
  const seen = new Set<string>();
  return result.filter((change) => {
    const key = `${change.original}\u0000${change.modified}\u0000${change.insert_after_text ?? ""}\u0000${change.paragraph_context ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function applyAutomaticPreflightFixes(text: string, checks: DocumentPreflightCheck[]) {
  let correctedText = text;
  const modifications: Modification[] = [];

  for (const check of checks) {
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
    });
  }

  return { correctedText, modifications };
}

function applyPreciselyLocatedChanges(
  sourceText: string,
  checks: DocumentPreflightCheck[],
  risks: ReviewRisk[],
) {
  const paragraphs = textToParagraphs(sourceText);
  const htmlParagraphs = getHtmlParagraphs(textToEditorHtml(sourceText));
  const modifications: Modification[] = [];
  const appliedItems: string[] = [];
  const proposed = [
    ...checks
      .filter((check) => check.auto_fixable && check.original_text && check.replacement_text)
      .map((check, index) => ({
        item: `基础修正：${check.title}`,
        riskKey: `preflight-${check.category}-${check.title}-${index}`,
        original: check.original_text!,
        modified: check.replacement_text!,
        source: "preflight" as const,
      })),
    ...risks
      .filter((risk) => !isMissingClause(risk.original_text) && Boolean(risk.original_text.trim()) && Boolean(risk.suggestion.trim()))
      .map((risk) => ({
        item: risk.item,
        riskKey: getRiskKey(risk),
        original: risk.original_text,
        modified: risk.suggestion,
        source: "risk" as const,
        risk,
      })),
  ];

  const plannedByParagraph = new Map<number, Array<typeof proposed[number] & { index: number }>>();

  for (const change of proposed) {
    const matches: Array<{ paragraphIndex: number; index: number }> = [];
    paragraphs.forEach((paragraph, paragraphIndex) => {
      let index = paragraph.indexOf(change.original);
      while (index >= 0) {
        matches.push({ paragraphIndex, index });
        index = paragraph.indexOf(change.original, index + change.original.length);
      }
    });
    // Prefer one exact occurrence. If a phrase repeats, a backend-verified
    // paragraph anchor (from the model's P-number reference) is still an
    // exact, unique context and can safely disambiguate it without asking the
    // user to select a candidate by hand. Free-form/fuzzy candidates never
    // enter this automatic path.
    let match = matches.length === 1 ? matches[0] : null;
    if (!match && change.source === "risk") {
      const verifiedParagraph = change.risk?.anchor_text?.trim();
      if (verifiedParagraph) {
        const anchoredMatches = matches.filter(({ paragraphIndex }) => (
          paragraphs[paragraphIndex] === verifiedParagraph
          || paragraphs[paragraphIndex].includes(verifiedParagraph)
        ));
        if (anchoredMatches.length === 1) {
          match = anchoredMatches[0];
        }
      }
    }
    if (!match) continue;
    const planned = plannedByParagraph.get(match.paragraphIndex) ?? [];
    planned.push({ ...change, index: match.index });
    plannedByParagraph.set(match.paragraphIndex, planned);
  }

  for (const [paragraphIndex, changes] of plannedByParagraph) {
    const originalParagraph = paragraphs[paragraphIndex];
    const accepted = [] as Array<typeof changes[number] & { revisionId: string }>;
    let occupiedUntil = -1;
    for (const change of [...changes].sort((left, right) => left.index - right.index || left.original.length - right.original.length)) {
      const end = change.index + change.original.length;
      if (change.index < occupiedUntil) continue;
      accepted.push({ ...change, revisionId: `auto-${paragraphIndex}-${modifications.length + accepted.length + 1}` });
      occupiedUntil = end;
    }
    if (!accepted.length) continue;

    let textCursor = 0;
    let correctedParagraph = "";
    let revisionHtml = "<p>";
    for (const change of accepted) {
      const prefix = originalParagraph.slice(textCursor, change.index);
      correctedParagraph += prefix + change.modified;
      revisionHtml += `${renderPlainTextFragment(prefix)}<del class="del-mark" data-revision-id="${escapeHtml(change.revisionId)}">${renderPlainTextFragment(change.original)}</del><ins class="ins-mark" data-revision-id="${escapeHtml(change.revisionId)}">${renderPlainTextFragment(change.modified)}</ins>`;
      textCursor = change.index + change.original.length;
      modifications.push({
        item: change.item,
        risk_key: change.riskKey,
        original: change.original,
        modified: change.modified,
        revision_id: change.revisionId,
        paragraph_context: originalParagraph,
        anchor_text: change.source === "risk" ? change.risk?.anchor_text ?? null : null,
        insert_after_text: change.source === "risk" ? change.risk?.insert_after_text ?? null : null,
      });
      if (change.source === "risk") appliedItems.push(change.item);
    }
    correctedParagraph += originalParagraph.slice(textCursor);
    revisionHtml += `${renderPlainTextFragment(originalParagraph.slice(textCursor))}</p>`;
    paragraphs[paragraphIndex] = correctedParagraph;
    htmlParagraphs[paragraphIndex] = revisionHtml;
  }

  return { correctedText: paragraphs.join("\n"), revisionHtml: htmlParagraphs.join(""), modifications, appliedItems };
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

function findUniqueExactMatch(text: string, query: string): { from: number; to: number } | null {
  if (!query) {
    return null;
  }

  const from = text.indexOf(query);
  if (from < 0 || text.indexOf(query, from + query.length) >= 0) {
    return null;
  }

  return { from, to: from + query.length };
}

function findRiskLocationCandidates(text: string, risk: ReviewRisk): RiskLocationCandidate[] {
  const query = risk.original_text.trim();
  const anchor = getInsertionAnchor(risk) ?? "";
  const paragraphs = textToParagraphs(text);
  const candidates: RiskLocationCandidate[] = [];
  let from = 0;

  paragraphs.forEach((paragraph, paragraphIndex) => {
    const exactIndex = query ? paragraph.indexOf(query) : -1;
    const anchorIndex = anchor ? paragraph.indexOf(anchor) : -1;
    const quoteScore = query.length >= 8 ? getParagraphMatchScore(paragraph, query) : 0;
    const anchorScore = anchor.length >= 8 ? getParagraphMatchScore(paragraph, anchor) : 0;
    const score = exactIndex >= 0 ? 1 : Math.max(anchorIndex >= 0 ? 0.96 : 0, quoteScore, anchorScore);

    if (score >= 0.62) {
      const reason: RiskLocationCandidate["reason"] = exactIndex >= 0
        ? "exact"
        : anchorIndex >= 0 || anchorScore > quoteScore
          ? "anchor"
          : "similar";
      const selectionFrom = exactIndex >= 0 ? exactIndex : 0;
      const selectionTo = exactIndex >= 0 ? exactIndex + query.length : paragraph.length;
      candidates.push({
        paragraph,
        paragraphIndex,
        from,
        to: from + paragraph.length,
        selectionFrom,
        selectionTo,
        score,
        reason,
        exactOriginal: exactIndex >= 0,
      });
    }
    from += paragraph.length + 1;
  });

  return candidates
    .sort((left, right) => right.score - left.score || left.paragraphIndex - right.paragraphIndex)
    .slice(0, 4);
}

function getRiskKey(risk: ReviewRisk) {
  return `${risk.item}\u0000${risk.original_text}\u0000${risk.suggestion}`;
}

function isRiskModification(modification: Modification, risk: ReviewRisk, riskKey: string) {
  if (modification.risk_key) {
    return modification.risk_key === riskKey;
  }
  return modification.item === risk.item
    && (modification.original === risk.original_text
      || (isMissingClause(risk.original_text) && modification.modified === risk.suggestion));
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

function buildReplacementDiffHtml(paragraphText: string, originalText: string, suggestion: string, preferredIndex?: number, revisionId?: string) {
  const revisionAttribute = revisionId ? ` data-revision-id="${escapeHtml(revisionId)}"` : "";
  const exactIndex = preferredIndex !== undefined && paragraphText.slice(preferredIndex, preferredIndex + originalText.length) === originalText
    ? preferredIndex
    : paragraphText.indexOf(originalText);
  if (exactIndex >= 0) {
    const prefix = paragraphText.slice(0, exactIndex);
    const suffix = paragraphText.slice(exactIndex + originalText.length);
    return `<p${revisionAttribute}>${renderPlainTextFragment(prefix)}<del class="del-mark"${revisionAttribute}>${renderPlainTextFragment(originalText)}</del><ins class="ins-mark"${revisionAttribute}>${renderPlainTextFragment(suggestion)}</ins>${renderPlainTextFragment(suffix)}</p>`;
  }

  return `<p${revisionAttribute}><del class="del-mark"${revisionAttribute}>${renderPlainTextFragment(paragraphText)}</del><ins class="ins-mark"${revisionAttribute}>${renderPlainTextFragment(suggestion)}</ins></p>`;
}

function buildInsertedParagraphHtml(suggestion: string, revisionId?: string) {
  const revisionAttribute = revisionId ? ` data-revision-id="${escapeHtml(revisionId)}"` : "";
  return `<p${revisionAttribute}><ins class="ins-mark"${revisionAttribute}>${renderPlainTextFragment(suggestion)}</ins></p>`;
}

function getRevisionOffsetInParagraph(html: string, revisionId: string) {
  if (typeof document === "undefined") return null;
  const container = document.createElement("div");
  container.innerHTML = html;
  let offset = 0;
  // Keep the value inside an object because TypeScript cannot infer writes
  // performed by the recursive DOM walk closure.
  const located: { value: { from: number; to: number } | null } = { value: null };

  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      offset += node.textContent?.length ?? 0;
      return;
    }
    if (!(node instanceof HTMLElement)) return;

    const tag = node.tagName.toLowerCase();
    if (tag === "del") return;
    if (tag === "ins" && node.getAttribute("data-revision-id") === revisionId) {
      const from = offset;
      offset += node.textContent?.length ?? 0;
      located.value = { from, to: offset };
      return;
    }
    for (const child of Array.from(node.childNodes)) visit(child);
  };

  for (const child of Array.from(container.childNodes)) visit(child);
  return located.value;
}

function removeRevisionMarkup(html: string, revisionId: string) {
  if (typeof document === "undefined") return null;
  const container = document.createElement("div");
  container.innerHTML = html;
  const inserts = Array.from(container.querySelectorAll("ins.ins-mark"))
    .filter((node) => node.getAttribute("data-revision-id") === revisionId);
  if (!inserts.length) return null;

  for (const insert of inserts) {
    const previous = insert.previousElementSibling;
    const deleted = previous?.matches("del.del-mark") && previous.getAttribute("data-revision-id") === revisionId
      ? previous
      : null;
    if (deleted?.parentNode) {
      while (deleted.firstChild) {
        deleted.parentNode.insertBefore(deleted.firstChild, deleted);
      }
      deleted.remove();
    }
    insert.remove();
  }
  return container.innerHTML;
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

async function getContractOverview(file: File): Promise<ContractOverviewResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/api/overview", {
    method: "POST",
    headers: apiHeaders(),
    body: formData
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Contract overview request failed with status ${response.status}.`);
  }
  const payload = await response.json() as Partial<ContractOverviewResponse>;
  if (typeof payload.contract_text !== "string" || !payload.contract_text.trim() || !payload.overview) {
    throw new Error("合同概览服务未返回可用的合同文本或概览内容。");
  }
  return {
    filename: typeof payload.filename === "string" ? payload.filename : file.name,
    contract_text: payload.contract_text.replace(unsupportedEditorCharacters, ""),
    overview: {
      contract_type: typeof payload.overview.contract_type === "string" ? payload.overview.contract_type : "待确认",
      summary: typeof payload.overview.summary === "string" ? payload.overview.summary : "已读取合同，请确认我方身份与业务诉求。",
      parties: Array.isArray(payload.overview.parties) ? payload.overview.parties.filter((item): item is string => typeof item === "string") : [],
      transaction_subject: typeof payload.overview.transaction_subject === "string" ? payload.overview.transaction_subject : "待确认",
      key_terms: Array.isArray(payload.overview.key_terms) ? payload.overview.key_terms.filter((item): item is string => typeof item === "string") : [],
      dimensions: Array.isArray(payload.overview.dimensions) ? payload.overview.dimensions.flatMap((item) => {
        if (!item || typeof item !== "object" || typeof item.category !== "string") return [];
        const status = item.status === "stated" || item.status === "partial" || item.status === "not_found" ? item.status : "not_found";
        return [{ category: item.category, status, details: Array.isArray(item.details) ? item.details.filter((detail): detail is string => typeof detail === "string") : [] }];
      }) : [],
      business_flow: Array.isArray(payload.overview.business_flow) ? payload.overview.business_flow.filter((item): item is string => typeof item === "string") : [],
      party_responsibilities: Array.isArray(payload.overview.party_responsibilities) ? payload.overview.party_responsibilities.flatMap((item) => {
        if (!item || typeof item !== "object" || typeof item.party !== "string") return [];
        return [{ party: item.party, responsibilities: Array.isArray(item.responsibilities) ? item.responsibilities.filter((duty): duty is string => typeof duty === "string") : [] }];
      }) : [],
      decision_points: Array.isArray(payload.overview.decision_points) ? payload.overview.decision_points.flatMap((item) => {
        if (!item || typeof item !== "object" || typeof item.topic !== "string") return [];
        return [{ topic: item.topic, contract_position: typeof item.contract_position === "string" ? item.contract_position : "", user_question: typeof item.user_question === "string" ? item.user_question : "" }];
      }) : [],
      clarification_questions: Array.isArray(payload.overview.clarification_questions) ? payload.overview.clarification_questions.filter((item): item is string => typeof item === "string") : [],
      method: payload.overview.method === "model" ? "model" : "fallback",
      warnings: Array.isArray(payload.overview.warnings) ? payload.overview.warnings.filter((item): item is string => typeof item === "string") : []
    },
    document_quality: payload.document_quality ?? null
  };
}

function normalizeIntakeCriteria(value: unknown): IntakeReviewCriteria {
  if (!value || typeof value !== "object") return { ...emptyIntakeCriteria };
  const source = value as Record<string, unknown>;
  const list = (key: string, limit: number) => Array.isArray(source[key])
    ? source[key].filter((item): item is string => typeof item === "string" && item.trim().length > 0).slice(0, limit)
    : [];
  return {
    party_role: source.party_role === "party_a" || source.party_role === "party_b" || source.party_role === "other" ? source.party_role : null,
    other_party_role: typeof source.other_party_role === "string" ? source.other_party_role : "",
    deal_priorities: list("deal_priorities", 6),
    focus_areas: list("focus_areas", 8),
    review_style: source.review_style === "balanced" || source.review_style === "material_only" ? source.review_style : "protective",
    business_context: typeof source.business_context === "string" ? source.business_context : "",
    non_negotiables: typeof source.non_negotiables === "string" ? source.non_negotiables : "",
    special_requirements: list("special_requirements", 8),
    additional_notes: list("additional_notes", 5)
  };
}

async function continueIntakeChat(
  overview: ContractOverviewResponse,
  messages: IntakeChatMessage[],
  criteria: IntakeReviewCriteria,
): Promise<IntakeChatResponse> {
  const response = await fetch("/api/intake/chat", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      contract_text: overview.contract_text,
      overview: overview.overview,
      messages: messages.map(({ role, content }) => ({ role, content })),
      criteria
    })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `法务助手对话请求失败（${response.status}）。`);
  }
  const payload = await response.json() as Partial<IntakeChatResponse>;
  if (typeof payload.assistant_message !== "string" || !payload.assistant_message.trim()) {
    throw new Error("法务助手未返回下一步问题，请重试。");
  }
  return {
    assistant_message: payload.assistant_message.trim(),
    quick_replies: Array.isArray(payload.quick_replies)
      ? payload.quick_replies.filter((item): item is string => typeof item === "string" && item.trim().length > 0).slice(0, 4)
      : [],
    suggested_questions: Array.isArray(payload.suggested_questions)
      ? payload.suggested_questions.filter((item): item is string => typeof item === "string" && item.trim().length > 0).slice(0, 4)
      : [],
    criteria: normalizeIntakeCriteria(payload.criteria),
    ready_for_review: payload.ready_for_review === true,
    source: payload.source === "model" ? "model" : "fallback",
    warning: typeof payload.warning === "string" ? payload.warning : null
  };
}

async function continueLegalResearch(
  messages: IntakeChatMessage[],
  contractContext?: string,
): Promise<LegalResearchResponse> {
  const response = await fetch("/api/legal-research/chat", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: messages.slice(-12).map(({ role, content }) => ({ role, content })),
      contract_context: contractContext?.slice(0, 12_000) ?? "",
    }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `法规咨询请求失败（${response.status}）。`);
  }
  const payload = await response.json() as Partial<LegalResearchResponse>;
  if (typeof payload.assistant_message !== "string" || !payload.assistant_message.trim()) {
    throw new Error("法规咨询未返回有效回答，请重试。");
  }
  return {
    assistant_message: payload.assistant_message.trim(),
    suggested_questions: Array.isArray(payload.suggested_questions)
      ? payload.suggested_questions.filter((item): item is string => typeof item === "string" && item.trim().length > 0).slice(0, 4)
      : [],
    source: payload.source === "model" ? "model" : "fallback",
    warning: typeof payload.warning === "string" ? payload.warning : null,
  };
}

const legalResearchSignals = [
  "法规", "法条", "法律", "条例", "办法", "规定", "司法解释", "民法典", "公司法", "劳动法",
  "个人信息保护法", "数据安全法", "网络安全法", "知识产权", "法律依据", "第几条", "条文",
];

function isLegalResearchQuestion(content: string) {
  const normalized = content.replace(/\s+/g, "");
  return legalResearchSignals.some((signal) => normalized.includes(signal));
}

async function reviewContractDeeply(
  filename: string,
  contractText: string,
  settings: DeepReviewSettings,
  documentQuality?: DocumentQuality,
): Promise<ReviewResponse> {
  const response = await fetch("/api/review/deep", {
    method: "POST",
    headers: { ...apiHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ filename, contract_text: contractText, settings, document_quality: documentQuality ?? null })
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
            (category !== "structure" && category !== "scope" && category !== "punctuation" && category !== "typo")
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

async function exportReviewedContract(file: File, modifications: Modification[]) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("modifications", JSON.stringify(modifications));
  formData.append("export_mode", "tracked");

  const response = await fetch("/api/export", {
    method: "POST",
    headers: apiHeaders(),
    body: formData
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Export request failed with status ${response.status}.`);
  }

  return {
    blob: await response.blob(),
    applied: Number(response.headers.get("X-Review-Applied-Modifications") ?? modifications.length),
    skipped: Number(response.headers.get("X-Review-Skipped-Modifications") ?? 0),
  };
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
  const highlightedRevisionNodesRef = useRef<HTMLElement[]>([]);
  const riskCardRefs = useRef<Record<string, HTMLElement | null>>({});
  const pendingRevisionHtmlRef = useRef<string | null>(null);
  const readerPanelRef = useRef<HTMLElement | null>(null);
  const intakeTimelineRef = useRef<HTMLDivElement | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [contractOverview, setContractOverview] = useState<ContractOverviewResponse | null>(null);
  const [modifications, setModifications] = useState<Modification[]>([]);
  const [editorText, setEditorText] = useState("");
  const [editorNotice, setEditorNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualInsertRiskKey, setManualInsertRiskKey] = useState<string | null>(null);
  const [manualInsertAfterText, setManualInsertAfterText] = useState("");
  const [selectedRiskLocations, setSelectedRiskLocations] = useState<Record<string, RiskLocationCandidate>>({});
  const [activeRiskKey, setActiveRiskKey] = useState<string | null>(null);
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("all");
  const [riskFeedback, setRiskFeedback] = useState<Record<string, FeedbackDecision>>({});
  const [preflightDecisions, setPreflightDecisions] = useState<Record<string, PreflightDecision>>({});
  const [reviewStage, setReviewStage] = useState<ReviewStage>("upload");
  const [deepReviewSettings, setDeepReviewSettings] = useState<DeepReviewFormSettings>({
    party_role: "",
    other_party_role: "",
    transaction_stage: "",
    timeline_urgency: "",
    counterparty_context: "",
    deal_priorities: [],
    focus_areas: [],
    review_style: "protective",
    contract_type: "",
    special_requirements: [],
    business_context: "",
    non_negotiables: "",
    additional_notes: []
  });
  const [intakeMessages, setIntakeMessages] = useState<IntakeChatMessage[]>([]);
  const [intakeCriteria, setIntakeCriteria] = useState<IntakeReviewCriteria>(emptyIntakeCriteria);
  const [intakeChatDraft, setIntakeChatDraft] = useState("");
  const [isIntakeChatLoading, setIsIntakeChatLoading] = useState(false);
  const [intakeReadyForReview, setIntakeReadyForReview] = useState(false);
  const [intakeChatWarning, setIntakeChatWarning] = useState<string | null>(null);
  const [additionalNoteDraft, setAdditionalNoteDraft] = useState("");
  const [intakeConversationStep, setIntakeConversationStep] = useState<IntakeConversationStep>("role");
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [readerPanelHeight, setReaderPanelHeight] = useState<number | null>(null);
  const syncingEditorRef = useRef(false);
  // Every file/reset starts a new review session.  Long-running overview,
  // intake and deep-review calls keep the session number they started with so
  // an older response can never overwrite a newer contract's workspace.
  const workflowEpochRef = useRef(0);

  const sortedRisks = useMemo(() => {
    return [...(review?.risks ?? [])].sort((left, right) => levelOrder[left.level] - levelOrder[right.level]);
  }, [review]);

  const preflightChecks = review?.preflight_checks ?? [];
  const intakeRecommendations = useMemo(
    () => contractOverview ? getIntakeRecommendations(contractOverview.overview) : null,
    [contractOverview],
  );
  const quickFocusOptions = useMemo(() => {
    const recommended = intakeRecommendations?.focus ?? [];
    const selected = deepReviewSettings.focus_areas;
    return Array.from(new Set([...recommended, ...selected, "价格与付款", "交付与验收", "责任与赔偿"])).slice(0, 4);
  }, [deepReviewSettings.focus_areas, intakeRecommendations]);
  const intakeInstructionSummary = useMemo(() => {
    const parts = [
      deepReviewSettings.party_role === "party_a" ? "以甲方/采购方立场" : deepReviewSettings.party_role === "party_b" ? "以乙方/供应商立场" : deepReviewSettings.party_role === "other" ? `以${deepReviewSettings.other_party_role || "自定义角色"}立场` : "待确认我方立场",
      deepReviewSettings.transaction_stage,
      deepReviewSettings.timeline_urgency,
      deepReviewSettings.counterparty_context,
    ].filter(Boolean);
    if (deepReviewSettings.deal_priorities.length) parts.push(`优先实现：${deepReviewSettings.deal_priorities.join("、")}`);
    if (deepReviewSettings.special_requirements.length) parts.push(`不可让步：${deepReviewSettings.special_requirements.join("、")}`);
    if (deepReviewSettings.additional_notes.length) parts.push(`已补充 ${deepReviewSettings.additional_notes.length} 条业务想法`);
    return parts.join("；");
  }, [deepReviewSettings]);
  const preflightWarnings = useMemo(
    () => preflightChecks.filter((check) => check.status === "warning"),
    [preflightChecks]
  );
  const risksWithKeys = useMemo<RiskWithKey[]>(() => {
    return sortedRisks.map((risk) => ({ risk, riskKey: getRiskKey(risk) }));
  }, [sortedRisks]);
  const unlocatableRisks = useMemo(
    () => risksWithKeys.filter(({ risk, riskKey }) => {
      const alreadyApplied = modifications.some((item) => isRiskModification(item, risk, riskKey));
      return !alreadyApplied
        && !isMissingClause(risk.original_text)
        && !findUniqueExactMatch(editorText, risk.original_text);
    }),
    [editorText, modifications, risksWithKeys]
  );

  const filteredRisks = useMemo(() => {
    if (riskFilter === "all") {
      return risksWithKeys;
    }

    if (riskFilter === "pending") {
      return risksWithKeys.filter(({ risk, riskKey }) => !modifications.some((item) => isRiskModification(item, risk, riskKey)));
    }

    if (riskFilter === "processed") {
      return risksWithKeys.filter(({ risk, riskKey }) => modifications.some((item) => isRiskModification(item, risk, riskKey)));
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
    () => risksWithKeys.filter(({ risk, riskKey }) => modifications.some((item) => isRiskModification(item, risk, riskKey))).length,
    [modifications, risksWithKeys],
  );

  const paragraphOptions = useMemo(() => normalizeParagraphs(editorText), [editorText]);
  const canSubmit = Boolean(file) && !isLoading;
  const hasEditorChanges = Boolean(review?.contract_text && editorText !== review.contract_text);
  const canExport = reviewStage === "modification" && Boolean(file) && Boolean(review) && (modifications.length > 0 || hasEditorChanges) && !isExporting;
  const totalRisks = sortedRisks.length;

  useEffect(() => {
    const timeline = intakeTimelineRef.current;
    if (!timeline || review) return;
    const frame = requestAnimationFrame(() => {
      timeline.scrollTo({ top: timeline.scrollHeight, behavior: "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [contractOverview, file, intakeMessages, intakeReadyForReview, isIntakeChatLoading, isLoading, review]);

  useEffect(() => {
    const panel = readerPanelRef.current;
    if (!panel || typeof ResizeObserver === "undefined") return;

    const updateHeight = () => {
      const nextHeight = Math.ceil(panel.getBoundingClientRect().height);
      setReaderPanelHeight((current) => current === nextHeight ? current : nextHeight);
    };
    const observer = new ResizeObserver(updateHeight);
    observer.observe(panel);
    updateHeight();
    return () => observer.disconnect();
  }, [review, editorNotice, error]);

  function clearEditorHighlight() {
    if (highlightedParagraphRef.current) {
      highlightedParagraphRef.current.classList.remove("contract-paragraph-highlight");
      highlightedParagraphRef.current = null;
    }
    for (const node of highlightedRevisionNodesRef.current) {
      node.classList.remove("contract-revision-highlight");
    }
    highlightedRevisionNodesRef.current = [];
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

          const exactMatches = risksWithKeys.filter((riskEntry) => {
            const candidate = isMissingClause(riskEntry.risk.original_text)
              ? getInsertionAnchor(riskEntry.risk) ?? ""
              : riskEntry.risk.original_text;
            return Boolean(candidate && paragraphText.includes(candidate) && findUniqueExactMatch(editorText, candidate));
          });

          if (exactMatches.length === 1) {
            const [exactMatch] = exactMatches;
            setActiveRiskKey(exactMatch.riskKey);
            riskCardRefs.current[exactMatch.riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
            setEditorNotice(`已在右侧定位到“${exactMatch.risk.item}”风险卡。`);
          } else if (exactMatches.length > 1) {
            setEditorNotice("当前段落对应多条风险，未自动选择风险卡；请在右侧手动核对。");
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
        // A completed review may already have exact automatic revisions.  Do
        // not overwrite their red/green marks with a later plain-text load.
        const html = pendingRevisionHtmlRef.current ?? textToEditorHtml(safeContractText);
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
    editor.setEditable(reviewStage === "modification");
  }, [editor, reviewStage]);

  function resetEditorState() {
    pendingRevisionHtmlRef.current = null;
    setModifications([]);
    setEditorNotice(null);
    setEditorText("");
    setManualInsertRiskKey(null);
    setManualInsertAfterText("");
    setSelectedRiskLocations({});
    setActiveRiskKey(null);
    setRiskFilter("all");
    setRiskFeedback({});
    setPreflightDecisions({});
    setDeepReviewSettings({
      party_role: "",
      other_party_role: "",
      transaction_stage: "",
      timeline_urgency: "",
      counterparty_context: "",
      deal_priorities: [],
      focus_areas: [],
      review_style: "protective",
      contract_type: "",
      special_requirements: [],
      business_context: "",
      non_negotiables: "",
      additional_notes: []
    });
    setAdditionalNoteDraft("");
    setIntakeConversationStep("role");
    setIntakeMessages([]);
    setIntakeCriteria(emptyIntakeCriteria);
    setIntakeChatDraft("");
    setIsIntakeChatLoading(false);
    setIntakeReadyForReview(false);
    setIntakeChatWarning(null);
    setReviewStage("upload");
    setContractOverview(null);
    setIsSidebarCollapsed(false);
    clearEditorHighlight();
    clearInsertionHighlight();
    editor?.commands.setContent(emptyEditorHtml);
  }

  function clearReview() {
    workflowEpochRef.current += 1;
    setFile(null);
    setReview(null);
    setContractOverview(null);
    setError(null);
    resetEditorState();
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleFileSelection(selectedFile: File | null) {
    workflowEpochRef.current += 1;
    setReview(null);
    setContractOverview(null);
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
    void startContractIntake(selectedFile);
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0] ?? null;
    handleFileSelection(selectedFile);
  }

  async function startContractIntake(selectedFile: File) {
    const workflowEpoch = ++workflowEpochRef.current;
    setIsLoading(true);
    setError(null);
    setReview(null);
    resetEditorState();

    try {
      const overview = await getContractOverview(selectedFile);
      if (workflowEpochRef.current !== workflowEpoch) return;
      setContractOverview(overview);
      setReviewStage("intake");
      setEditorNotice(null);
      await requestIntakeAssistant(overview, [], emptyIntakeCriteria, workflowEpoch);
    } catch (submitError) {
      if (workflowEpochRef.current !== workflowEpoch) return;
      setError(getErrorMessage(submitError));
    } finally {
      if (workflowEpochRef.current === workflowEpoch) {
        setIsLoading(false);
      }
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

  function revealAppliedRevision(modification: Modification) {
    if (!editor || editor.isDestroyed) {
      return false;
    }

    const editorRoot = editor.view.dom;
    const allRevisionMarks = Array.from(
      editorRoot.querySelectorAll<HTMLElement>("ins.ins-mark, del.del-mark")
    );
    let marks = modification.revision_id
      ? allRevisionMarks.filter((mark) => mark.dataset.revisionId === modification.revision_id)
      : [];

    // Older review results created before revision IDs still get an exact
    // text-based fallback. It requires both sides of a replacement where
    // possible, so a repeated suggestion cannot jump to an unrelated mark.
    if (!marks.length) {
      const insertedMarks = allRevisionMarks.filter((mark) => (
        mark.matches("ins.ins-mark") && mark.textContent?.trim() === modification.modified.trim()
      ));
      marks = insertedMarks.filter((mark) => {
        const paragraph = mark.closest("p");
        if (!paragraph) return false;
        if (isMissingClause(modification.original)) return true;
        return Array.from(paragraph.querySelectorAll("del.del-mark"))
          .some((deleted) => deleted.textContent?.trim() === modification.original.trim());
      });
    }

    if (marks.length !== 1 && marks.length !== 2) {
      return false;
    }

    const target = marks.find((mark) => mark.matches("ins.ins-mark")) ?? marks[0];
    const paragraph = target.closest("p");
    clearEditorHighlight();
    marks.forEach((mark) => mark.classList.add("contract-revision-highlight"));
    highlightedRevisionNodesRef.current = marks;
    if (paragraph instanceof HTMLElement) {
      paragraph.classList.add("contract-paragraph-highlight");
      highlightedParagraphRef.current = paragraph;
    }

    try {
      const textNode = target.firstChild;
      if (textNode) {
        const from = editor.view.posAtDOM(textNode, 0);
        const to = editor.view.posAtDOM(textNode, textNode.textContent?.length ?? 0);
        editor.commands.focus();
        editor.commands.setTextSelection({ from, to });
      }
    } catch {
      // Visual focus remains accurate even when a browser does not expose a
      // selectable DOM text node for a revision mark.
    }

    (paragraph ?? target).scrollIntoView({ behavior: "smooth", block: "center" });
    setEditorNotice(`已精确定位“${modification.item ?? "该项"}”的修订痕迹：红线为原文，绿色为修改后文本。`);
    return true;
  }

  function locateRiskInEditor(risk: ReviewRisk) {
    const candidate = isMissingClause(risk.original_text) ? getInsertionAnchor(risk) ?? "" : risk.original_text;
    if (!candidate) {
      return false;
    }

    const exactMatch = findUniqueExactMatch(editorText, candidate);
    if (!exactMatch) {
      const candidates = findRiskLocationCandidates(editorText, risk);
      const bestCandidate = candidates[0];
      if (!bestCandidate) {
        return false;
      }
      revealEditorSelection(bestCandidate.from + bestCandidate.selectionFrom + 1, bestCandidate.from + bestCandidate.selectionTo + 1);
      setEditorNotice(
        bestCandidate.exactOriginal
          ? `已找到“${risk.item}”的候选原文。请在右侧确认该段后再引用修改。`
          : `已定位到“${risk.item}”的可能段落（${bestCandidate.reason === "anchor" ? "按邻近条款定位" : "按文字相似度定位"}）。请核对原文后手动编辑。`
      );
      return true;
    }

    revealEditorSelection(exactMatch.from + 1, exactMatch.to + 1);
    return true;
  }

  function focusRisk(risk: ReviewRisk, riskKey: string) {
    setActiveRiskKey(riskKey);
    const appliedModification = modifications.find((item) => isRiskModification(item, risk, riskKey));
    if (appliedModification && revealAppliedRevision(appliedModification)) {
      riskCardRefs.current[riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const located = locateRiskInEditor(risk);
    if (!located) {
      setEditorNotice("未能在当前正文中定位该风险的引用原文。系统不会自动改写，请核对原件后手动编辑。");
    }
    riskCardRefs.current[riskKey]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function selectRiskLocation(risk: ReviewRisk, riskKey: string, candidate: RiskLocationCandidate) {
    setActiveRiskKey(riskKey);
    setSelectedRiskLocations((previous) => ({ ...previous, [riskKey]: candidate }));
    revealEditorSelection(candidate.from + candidate.selectionFrom + 1, candidate.from + candidate.selectionTo + 1);
    setEditorNotice(
      candidate.exactOriginal
        ? `已确认“${risk.item}”的候选原文。现在可以在该段引用修改建议。`
        : `已定位到“${risk.item}”的可能段落。该段仅供核对，不会自动替换相似文字。`
    );
    setError(null);
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
    const anchorMatch = anchor ? findUniqueExactMatch(currentText, anchor) : null;
    const anchorMeta = anchorMatch ? getParagraphMetaFromOffset(currentText, anchorMatch.from) : null;

    if (anchor && !anchorMeta) {
      setError("未能精确定位所选插入段落，或该段文字在合同中重复出现。为避免插入到错误位置，请在左侧手动编辑。");
      return;
    }

    const nextParagraphs = textToParagraphs(currentText);
    const insertAtIndex = anchorMeta ? anchorMeta.index + 1 : nextParagraphs.length;
    const revisionId = `risk-${riskKey}`;
    nextParagraphs.splice(insertAtIndex, 0, risk.suggestion);

    const nextHtmlParagraphs = [...htmlParagraphs];
    nextHtmlParagraphs.splice(insertAtIndex, 0, buildInsertedParagraphHtml(risk.suggestion, revisionId));

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
      ...previous.filter((item) => !isRiskModification(item, risk, riskKey)),
      {
        item: risk.item,
        risk_key: riskKey,
        original: MISSING_SENTINEL,
        modified: risk.suggestion,
        revision_id: revisionId,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: anchorText ?? risk.insert_after_text ?? risk.anchor_text ?? null
      }
    ]);
    void submitFeedback(risk, riskKey, "edited", risk.suggestion);

    const insertedOffset = nextParagraphs.slice(0, insertAtIndex).join("\n").length + (insertAtIndex > 0 ? 1 : 0);
    revealEditorSelection(Math.max(1, insertedOffset + 1), Math.max(1, insertedOffset + risk.suggestion.length + 1));
  }

  function applySuggestionAtSelectedLocation(risk: ReviewRisk, riskKey: string, candidate: RiskLocationCandidate) {
    if (!editor || !candidate.exactOriginal) {
      setError("请先选择一段包含完整原文的候选条款；相似匹配只能用于定位，不能自动改写。");
      return;
    }

    const paragraphs = textToParagraphs(editorText);
    const paragraphText = paragraphs[candidate.paragraphIndex];
    const original = risk.original_text;
    const index = candidate.selectionFrom;
    if (!paragraphText || paragraphText !== candidate.paragraph || paragraphText.slice(index, index + original.length) !== original) {
      setSelectedRiskLocations((previous) => {
        const next = { ...previous };
        delete next[riskKey];
        return next;
      });
      setError("候选段落已发生变化，请重新定位并确认后再引用修改。");
      return;
    }

    const nextParagraph = paragraphText.slice(0, index) + risk.suggestion + paragraphText.slice(index + original.length);
    const nextText = [...paragraphs.slice(0, candidate.paragraphIndex), nextParagraph, ...paragraphs.slice(candidate.paragraphIndex + 1)].join("\n");
    const htmlParagraphs = getHtmlParagraphs(editor.getHTML());
    if (!htmlParagraphs[candidate.paragraphIndex]) {
      setError("正文段落结构已变化，请重新定位后再试。");
      return;
    }
    const revisionId = `risk-${riskKey}`;
    htmlParagraphs[candidate.paragraphIndex] = buildReplacementDiffHtml(paragraphText, original, risk.suggestion, index, revisionId);
    editor.commands.setContent(htmlParagraphs.join(""));
    setEditorText(nextText);
    setSelectedRiskLocations((previous) => {
      const next = { ...previous };
      delete next[riskKey];
      return next;
    });
    setError(null);
    setEditorNotice(`已在您确认的段落中引用“${risk.item}”的修改建议。`);
    setModifications((previous) => [
      ...previous.filter((item) => !isRiskModification(item, risk, riskKey)),
      {
        item: risk.item,
        risk_key: riskKey,
        original,
        modified: risk.suggestion,
        revision_id: revisionId,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: risk.insert_after_text ?? null,
        paragraph_context: paragraphText,
      }
    ]);
    void submitFeedback(risk, riskKey, "edited", risk.suggestion);
    revealEditorSelection(Math.max(1, candidate.from + index + 1), Math.max(1, candidate.from + index + risk.suggestion.length + 1));
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
      const anchorMatch = anchor ? findUniqueExactMatch(currentText, anchor) : null;

      if (anchorMatch) {
        applyMissingSuggestion(risk, riskKey, anchor);
        return;
      }

      setManualInsertRiskKey(riskKey);
      setManualInsertAfterText(paragraphOptions[0]?.anchor ?? "");
      clearInsertionHighlight();
      setEditorNotice(`“${risk.item}” 暂未锁定插入位置，请选择要插入到哪一段后面。`);
      setError(null);
      return;
    }

    const originalMatch = findUniqueExactMatch(currentText, risk.original_text);
    if (!originalMatch) {
      const selectedCandidate = selectedRiskLocations[riskKey];
      if (selectedCandidate?.exactOriginal) {
        applySuggestionAtSelectedLocation(risk, riskKey, selectedCandidate);
        return;
      }
      setError("请先在下方候选段落中确认包含完整原文的一段；相似匹配仅用于辅助定位，不会自动替换。");
      return;
    }
    const originalIndex = originalMatch.from;

    const paragraphMeta = getParagraphMetaFromOffset(currentText, originalIndex);
    if (!paragraphMeta) {
      setError("未能定位对应段落，请稍后重试。");
      return;
    }

    const nextText =
      currentText.slice(0, originalIndex) + risk.suggestion + currentText.slice(originalIndex + risk.original_text.length);
    const currentHtml = editor.getHTML();
    const htmlParagraphs = getHtmlParagraphs(currentHtml);
    const nextHtmlParagraphs = [...htmlParagraphs];
    const revisionId = `risk-${riskKey}`;
    nextHtmlParagraphs[paragraphMeta.index] = buildReplacementDiffHtml(
      paragraphMeta.text,
      risk.original_text,
      risk.suggestion,
      undefined,
      revisionId,
    );

    editor.commands.setContent(nextHtmlParagraphs.join(""));
    setEditorText(nextText);
    setError(null);
    setEditorNotice(`已引用“${risk.item}”的修改建议。`);
    setModifications((previous) => [
      ...previous.filter((item) => !isRiskModification(item, risk, riskKey)),
      {
        item: risk.item,
        risk_key: riskKey,
        original: risk.original_text,
        modified: risk.suggestion,
        revision_id: revisionId,
        anchor_text: risk.anchor_text ?? null,
        insert_after_text: risk.insert_after_text ?? null,
        paragraph_context: paragraphMeta.text,
      }
    ]);
    void submitFeedback(risk, riskKey, "edited", risk.suggestion);

    revealEditorSelection(Math.max(1, originalIndex + 1), Math.max(1, originalIndex + risk.suggestion.length + 1));
  }

  function undoRiskModification(risk: ReviewRisk, riskKey: string) {
    const applied = modifications.find((item) => isRiskModification(item, risk, riskKey));
    if (!applied) return;
    if (!editor || !applied.revision_id) {
      setError("无法自动撤销：未找到本项修订标识，请在左侧正文中手动恢复原文。");
      return;
    }

    const htmlParagraphs = getHtmlParagraphs(editor.getHTML());
    const paragraphIndex = htmlParagraphs.findIndex((paragraph) => paragraph.includes(`data-revision-id="${escapeHtml(applied.revision_id!)}"`));
    const revisionParagraph = paragraphIndex >= 0 ? htmlParagraphs[paragraphIndex] : null;
    if (!revisionParagraph) {
      setError("无法自动撤销：该修订痕迹已被手动改动，请在左侧正文中恢复原文。");
      return;
    }

    const paragraphs = textToParagraphs(editorText);
    if (applied.original === MISSING_SENTINEL) {
      htmlParagraphs.splice(paragraphIndex, 1);
      paragraphs.splice(paragraphIndex, 1);
    } else {
      const position = getRevisionOffsetInParagraph(revisionParagraph, applied.revision_id);
      const nextRevisionParagraph = removeRevisionMarkup(revisionParagraph, applied.revision_id);
      const currentParagraph = paragraphs[paragraphIndex];
      if (!position || !nextRevisionParagraph || !currentParagraph || currentParagraph.slice(position.from, position.to) !== applied.modified) {
        setError("无法自动撤销：该修订痕迹已被手动改动，请在左侧正文中恢复原文。");
        return;
      }
      paragraphs[paragraphIndex] = currentParagraph.slice(0, position.from) + applied.original + currentParagraph.slice(position.to);
      htmlParagraphs[paragraphIndex] = nextRevisionParagraph;
    }

    editor.commands.setContent(htmlParagraphs.join(""));
    setEditorText(paragraphs.join("\n"));
    setModifications((previous) => previous.filter((item) => !isRiskModification(item, risk, riskKey)));
    setEditorNotice(`已撤销“${risk.item}”的系统修改；其他已应用内容保持不变。`);
    setError(null);
  }

  function toggleDeepSettingOption(field: "deal_priorities" | "focus_areas" | "special_requirements", option: string) {
    setDeepReviewSettings((current) => {
      const selected = current[field];
      const limit = field === "deal_priorities" ? 6 : 8;
      if (!selected.includes(option) && selected.length >= limit) {
        setError(`“${field === "deal_priorities" ? "交易目标" : field === "focus_areas" ? "重点关注" : "不可让步事项"}”最多选择 ${limit} 项，请先取消不适用的选项。`);
        return current;
      }
      const next = selected.includes(option)
        ? selected.filter((item) => item !== option)
        : [...selected, option];
      setError(null);
      return { ...current, [field]: next };
    });
  }

  function applyScenarioPreset(preset: typeof scenarioPresets[number]) {
    setDeepReviewSettings((current) => ({
      ...current,
      contract_type: current.contract_type || preset.contractType,
      deal_priorities: Array.from(new Set([...current.deal_priorities, ...preset.priorities])).slice(0, 6),
      focus_areas: Array.from(new Set([...current.focus_areas.filter((item) => item !== "全部"), ...preset.focus])).slice(0, 8),
      special_requirements: Array.from(new Set([...current.special_requirements, ...preset.requirements])).slice(0, 8),
    }));
    setEditorNotice(`已载入“${preset.name}”的常见审查重点。请取消不适用的选项；它们仅代表审查偏好，不会被视为合同事实。`);
    setError(null);
  }

  function addAdditionalNote() {
    const note = additionalNoteDraft.trim();
    if (!note) return;
    if (note.length > 500) {
      setError("单条补充内容请控制在 500 字以内，便于模型准确理解。");
      return;
    }
    if (deepReviewSettings.additional_notes.includes(note)) {
      setError("这条补充已添加，无需重复发送。");
      return;
    }
    if (deepReviewSettings.additional_notes.length >= 5) {
      setError("最多可补充 5 条想法，请合并或删除不再适用的内容。 ");
      return;
    }
    setDeepReviewSettings((current) => ({ ...current, additional_notes: [...current.additional_notes, note] }));
    setAdditionalNoteDraft("");
    setError(null);
  }

  function removeAdditionalNote(note: string) {
    setDeepReviewSettings((current) => ({
      ...current,
      additional_notes: current.additional_notes.filter((item) => item !== note),
    }));
  }

  function applyRecommendedFocus() {
    if (!intakeRecommendations) return;
    setDeepReviewSettings((current) => ({
      ...current,
      focus_areas: Array.from(new Set([...current.focus_areas.filter((item) => item !== "全部"), ...intakeRecommendations.focus])).slice(0, 8),
      special_requirements: Array.from(new Set([...current.special_requirements, ...intakeRecommendations.requirements])).slice(0, 8),
      contract_type: current.contract_type || contractOverview?.overview.contract_type || "",
    }));
    setEditorNotice("已采用合同概览建议。这些是审查优先级，不会被当作您已确认的商业事实或不可让步底线。");
  }

  function applyGuidedProfile(role: Extract<PartyRole, "party_a" | "party_b">) {
    const roleRequirements = role === "party_a"
      ? ["控制预付款", "保留验收权", "限制责任", "保留审计权"]
      : ["限制责任", "保护品牌与宣传权", "争议在我方所在地"];
    const rolePriorities = role === "party_a"
      ? ["按期上线或拿到可用成果", "预算可控，付款与结果挂钩", "降低违约、售后与退出成本"]
      : ["按期上线或拿到可用成果", "优先促成签约，保留必要保护", "降低违约、售后与退出成本"];
    setDeepReviewSettings((current) => ({
      ...current,
      party_role: role,
      deal_priorities: Array.from(new Set([...current.deal_priorities, ...rolePriorities])).slice(0, 6),
      focus_areas: Array.from(new Set([
        ...current.focus_areas.filter((item) => item !== "全部"),
        ...(intakeRecommendations?.focus ?? ["价格与付款", "交付与验收", "责任与赔偿"]),
      ])).slice(0, 8),
      special_requirements: Array.from(new Set([...current.special_requirements, ...roleRequirements, ...(intakeRecommendations?.requirements ?? [])])).slice(0, 8),
      review_style: "protective",
      contract_type: current.contract_type || contractOverview?.overview.contract_type || "",
    }));
    setEditorNotice(`已载入${role === "party_a" ? "甲方/采购方" : "乙方/供应商"}常见审查方案。请把其中不适用的选项取消；未填写的业务事实仍会在审查结果中提示您确认。`);
  }

  function answerIntakeRole(role: PartyRole) {
    // The role is a fact supplied by the user. Do not silently preload a
    // bundle of "red lines" merely because a role was selected; the assistant
    // should ask for the user's real commercial objective first.
    setDeepReviewSettings((current) => ({
      ...current,
      party_role: role,
      contract_type: current.contract_type || contractOverview?.overview.contract_type || "",
    }));
    setIntakeConversationStep(role === "other" ? "role" : "objective");
    setError(null);
  }

  function continueIntakeObjective() {
    setIntakeConversationStep("focus");
    setError(null);
  }

  function continueIntakeFocus() {
    setIntakeConversationStep("redlines");
    setError(null);
  }

  function answerIntakeStyle(style: ReviewStyle) {
    setDeepReviewSettings((current) => ({ ...current, review_style: style }));
    setIntakeConversationStep("ready");
    setError(null);
  }

  async function requestIntakeAssistant(
    overview: ContractOverviewResponse,
    messages: IntakeChatMessage[],
    criteria: IntakeReviewCriteria,
    workflowEpoch = workflowEpochRef.current,
  ) {
    setIsIntakeChatLoading(true);
    setIntakeChatWarning(null);
    try {
      const response = await continueIntakeChat(overview, messages, criteria);
      if (workflowEpochRef.current !== workflowEpoch) return;
      const nextMessages = [...messages, {
        role: "assistant" as const,
        content: response.assistant_message,
        intent: "intake" as const,
        quick_replies: response.quick_replies,
        suggested_questions: response.suggested_questions,
      }].slice(-12);
      setIntakeMessages(nextMessages);
      setIntakeCriteria(response.criteria);
      setDeepReviewSettings(criteriaToDeepReviewSettings(response.criteria, overview.overview));
      setIntakeReadyForReview(response.ready_for_review);
      setIntakeChatWarning(response.warning ?? null);
      setError(null);
    } catch (chatError) {
      if (workflowEpochRef.current !== workflowEpoch) return;
      setError(getErrorMessage(chatError));
      setIntakeChatWarning("法务助手暂时没有回应。您可以重试；合同内容和已输入的回答都会保留。");
    } finally {
      if (workflowEpochRef.current === workflowEpoch) {
        setIsIntakeChatLoading(false);
      }
    }
  }

  async function handleSubmit(event?: FormEvent) {
    if (event) {
      event.preventDefault();
    }

    if (!file) {
      setError("请先选择一份 .docx 或 .pdf 合同。");
      return;
    }

    await startContractIntake(file);
  }

  async function requestLegalResearchAssistant(
    messages: IntakeChatMessage[],
    workflowEpoch = workflowEpochRef.current,
  ) {
    setIsIntakeChatLoading(true);
    setIntakeChatWarning(null);
    try {
      const response = await continueLegalResearch(
        messages.filter((message) => message.intent === "legal_research").slice(-12),
        contractOverview ? `${contractOverview.overview.summary}\n\n${contractOverview.contract_text}` : undefined,
      );
      if (workflowEpochRef.current !== workflowEpoch) return;
      setIntakeMessages((current) => [...current, {
        role: "assistant" as const,
        content: response.assistant_message,
        intent: "legal_research" as const,
        suggested_questions: response.suggested_questions,
      }].slice(-12));
      setIntakeChatWarning(response.warning ?? null);
      setError(null);
    } catch (chatError) {
      if (workflowEpochRef.current !== workflowEpoch) return;
      setError(getErrorMessage(chatError));
      setIntakeChatWarning("法规咨询暂时没有回应。您可以重试；合同和既定审核方案不会改变。");
    } finally {
      if (workflowEpochRef.current === workflowEpoch) {
        setIsIntakeChatLoading(false);
      }
    }
  }

  async function submitIntakeChatAnswer(answer: string, preferredIntent?: "intake" | "legal_research") {
    if (isIntakeChatLoading) return;
    const content = answer.trim();
    if (!content) return;
    const lastAssistant = [...intakeMessages].reverse().find((message) => message.role === "assistant");
    const intent = preferredIntent
      ?? (!contractOverview || isLegalResearchQuestion(content) || lastAssistant?.intent === "legal_research" ? "legal_research" : "intake");
    const nextMessages = [...intakeMessages, { role: "user" as const, content, intent }].slice(-12);
    setIntakeMessages(nextMessages);
    setIntakeChatDraft("");
    if (intent === "legal_research") {
      await requestLegalResearchAssistant(nextMessages, workflowEpochRef.current);
      return;
    }
    if (contractOverview) {
      await requestIntakeAssistant(contractOverview, nextMessages, intakeCriteria, workflowEpochRef.current);
    }
  }

  async function sendIntakeChatMessage(event?: FormEvent) {
    event?.preventDefault();
    await submitIntakeChatAnswer(intakeChatDraft);
  }

  function stopIntakeDraft() {
    // Invalidate any response still in flight. Its result will be ignored, and
    // the unconfirmed last user message is removed from the next model prompt.
    workflowEpochRef.current += 1;
    setIsLoading(false);
    setIsIntakeChatLoading(false);
    setIntakeChatDraft("");
    setIntakeMessages((current) => {
      const lastMessage = current[current.length - 1];
      return lastMessage?.role === "user" ? current.slice(0, -1) : current;
    });
    setError(null);
    setIntakeChatWarning(null);
  }

  async function runDeepReview() {
    if (!contractOverview) return;
    if (!deepReviewSettings.party_role) {
      setError("请先选择我方在合同中的身份；深度审查不能默认合同立场。");
      return;
    }
    if (deepReviewSettings.party_role === "other" && !deepReviewSettings.other_party_role.trim()) {
      setError("请选择“其他”身份后，请说明我方在本合同中的角色。");
      return;
    }

    const workflowEpoch = workflowEpochRef.current;
    const settingsForReview: DeepReviewFormSettings = deepReviewSettings;
    setIsLoading(true);
    setError(null);
    try {
      const result = await reviewContractDeeply(
        contractOverview.filename,
        contractOverview.contract_text,
        settingsForReview as DeepReviewSettings,
        contractOverview.document_quality ?? undefined,
      );
      if (workflowEpochRef.current !== workflowEpoch) return;
      if (!result.deep_review || result.deep_review.state !== "completed" || !result.deep_review.executive_summary.trim()) {
        throw new Error("深度审查未返回完整的审查说明，系统未开放修改与导出。");
      }

      const autoApplied = applyPreciselyLocatedChanges(
        result.contract_text ?? contractOverview.contract_text,
        result.preflight_checks ?? [],
        result.risks,
      );
      pendingRevisionHtmlRef.current = autoApplied.revisionHtml;
      setReview({ ...result, contract_text: result.contract_text ?? contractOverview.contract_text, manual_review_required: true });
      setContractOverview(null);
      setModifications(autoApplied.modifications);
      setEditorText(autoApplied.correctedText);
      setReviewStage("modification");
      setEditorNotice(
        autoApplied.modifications.length
          ? `综合审查已完成；已自动定位并写入 ${autoApplied.modifications.length} 处可精确匹配的修改。右侧可逐项撤销；未唯一定位的建议保留为人工确认。`
          : "综合审查已完成。未发现可唯一定位的自动修改；请在右侧确认候选段落后再处理建议。"
      );
    } catch (reviewError) {
      if (workflowEpochRef.current !== workflowEpoch) return;
      setError(getErrorMessage(reviewError));
      setEditorNotice("深度审查未形成可验证结果，正文仍保持锁定；请检查模型服务后重试。");
    } finally {
      if (workflowEpochRef.current === workflowEpoch) {
        setIsLoading(false);
      }
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

  function setPreflightDecision(checkKey: string, decision: PreflightDecision, title: string) {
    setPreflightDecisions((previous) => ({ ...previous, [checkKey]: decision }));
    setEditorNotice(
      decision === "confirmed"
        ? `已确认“${title}”。该项仅记录为已核对，不会擅自修改合同正文。`
        : `已将“${title}”标记为暂不处理；它会保留在本次审查记录中。`
    );
  }

  async function copySuggestionToClipboard(risk: ReviewRisk) {
    try {
      await navigator.clipboard.writeText(risk.suggestion);
      setEditorNotice(`已复制“${risk.item}”的修改建议。请在确认对应原文后手动粘贴或编辑。`);
    } catch {
      setError("无法复制修改建议。请直接从右侧卡片选择并复制文字。");
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
    const exportModifications = collectExportModifications(modifications, editorModifications);

    if (!exportModifications.length) {
      setError("请先在正文中完成至少一处修改，或在右侧采用一条建议。");
      return;
    }

    setIsExporting(true);
    setError(null);

    try {
      const exportResult = await exportReviewedContract(file, exportModifications);
      downloadBlob(exportResult.blob, "reviewed_contract.docx");
      setEditorNotice(
        exportResult.skipped > 0
          ? `Word 审阅版已生成：已写入 ${exportResult.applied} 处可精确定位的修改；${exportResult.skipped} 条未采纳或无法回写的建议已跳过，仍保留在右侧供后续处理。`
          : "Word 审阅版已生成并开始下载：已采纳的修改保留修订痕迹，可在 Word 的“审阅”选项卡中接受或拒绝修改。"
      );
    } catch (exportError) {
      setError(getErrorMessage(exportError));
    } finally {
      setIsExporting(false);
    }
  }

  const currentFilename = review?.filename ?? file?.name ?? "未选择合同";
  const currentFileSize = file ? formatFileSize(file.size) : null;

  const renderIntakeWorkspace = () => (
    <section
      className="legal-chat-shell legal-chat-shell-openc"
      aria-busy={isLoading || isIntakeChatLoading}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        if (isLoading || isIntakeChatLoading) return;
        const droppedFile = event.dataTransfer.files?.[0];
        if (droppedFile) handleFileSelection(droppedFile);
      }}
    >
      <div className="legal-chat-timeline" aria-live="polite" ref={intakeTimelineRef}>
        {!contractOverview ? (
          <section className="legal-chat-welcome" aria-label="开始合同审查">
            <h1>今天需要处理什么法律问题？</h1>
            <p>上传 Word 或 PDF 可开始合同审查；也可直接咨询法规、法条与合同条款问题。</p>
          </section>
        ) : null}

        {!contractOverview ? (
          <article className="legal-chat-message legal-chat-message-assistant">
            <span className="legal-chat-message-avatar" aria-hidden="true">AI</span>
            <div className="legal-chat-message-body">
              <b>AI 法务助手</b>
              <p>{file ? "合同已加入会话，正在读取并提炼合同内容，随后开始确认审查方向。" : "您可以直接咨询法规、法条与合同问题；上传合同后，我会在不改变既定审查方案的前提下继续协助。"}</p>
            </div>
          </article>
        ) : null}

        {file ? (
          <article className="legal-chat-message legal-chat-message-user">
            <div className="legal-chat-message-body legal-chat-file-message">
              <span className="legal-chat-file-icon" aria-hidden="true">DOC</span>
              <div>
                <b>{file.name}</b>
                <span>{formatFileSize(file.size)} · {contractOverview ? "合同已读取" : isLoading ? "正在读取" : "已添加到会话"}</span>
              </div>
              <button type="button" disabled={isLoading || isIntakeChatLoading} onClick={() => fileInputRef.current?.click()}>更换</button>
            </div>
          </article>
        ) : null}

        {isLoading && !contractOverview ? (
          <article className="legal-chat-message legal-chat-message-assistant legal-chat-message-working">
            <span className="legal-chat-message-avatar" aria-hidden="true">AI</span>
            <div className="legal-chat-message-body"><b>AI 法务助手</b><p>正在解析合同、识别交易结构并准备第一个问题…</p></div>
          </article>
        ) : null}

        {contractOverview ? (
          <article className="legal-chat-message legal-chat-message-assistant">
            <span className="legal-chat-message-avatar" aria-hidden="true">AI</span>
            <div className="legal-chat-message-body legal-chat-overview-message">
              <b>合同已读取 · {contractOverview.overview.contract_type || "待确认合同类型"}</b>
              <p>{contractOverview.overview.summary}</p>
              {contractOverview.document_quality ? <small>文本质量：{contractOverview.document_quality.note}</small> : null}
              {contractOverview.overview.warnings.length ? (
                <div className="legal-chat-warning">{contractOverview.overview.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>
              ) : null}
            </div>
          </article>
        ) : null}

        {intakeMessages.map((message, index) => {
          const isLatestMessage = index === intakeMessages.length - 1;
          const quickReplies = message.role === "assistant" && isLatestMessage && !isIntakeChatLoading
            ? message.quick_replies ?? []
            : [];
          const suggestedQuestions = message.role === "assistant" && isLatestMessage && !isIntakeChatLoading
            ? message.suggested_questions ?? []
            : [];
          return (
            <article className={`legal-chat-message legal-chat-message-${message.role}`} key={`${message.role}-${index}-${message.content.slice(0, 20)}`}>
              {message.role === "assistant" ? <span className="legal-chat-message-avatar" aria-hidden="true">AI</span> : null}
              <div className="legal-chat-message-body">
                <b>{message.role === "assistant" ? "AI 法务助手" : "您"}</b>
                <p>{message.content}</p>
                {quickReplies.length ? (
                  <div className="legal-chat-quick-replies" aria-label="快捷回答">
                    {quickReplies.map((reply) => (
                      <button
                        key={reply}
                        type="button"
                        disabled={isLoading || isIntakeChatLoading}
                        onClick={() => void submitIntakeChatAnswer(reply)}
                      >
                        {reply}
                      </button>
                    ))}
                  </div>
                ) : null}
                {suggestedQuestions.length ? (
                  <div className="legal-chat-suggested-questions" aria-label="您接下来可能想问">
                    <span>您接下来可能想问</span>
                    <div>
                      {suggestedQuestions.map((question) => (
                        <button
                          key={question}
                          type="button"
                          disabled={isLoading || isIntakeChatLoading}
                          onClick={() => void submitIntakeChatAnswer(question)}
                        >
                          {question}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </article>
          );
        })}

        {isIntakeChatLoading ? (
          <article className="legal-chat-message legal-chat-message-assistant legal-chat-message-working">
            <span className="legal-chat-message-avatar" aria-hidden="true">AI</span>
            <div className="legal-chat-message-body"><b>AI 法务助手</b><p>{intakeMessages[intakeMessages.length - 1]?.intent === "legal_research" ? "正在整理法规信息与合同提示…" : "正在理解您的诉求并更新审核方案…"}</p></div>
          </article>
        ) : null}

        {intakeReadyForReview ? (
          <article className="legal-chat-plan" aria-label="已生成的审核方案">
            <div className="legal-chat-plan-heading">
              <div><span>已生成审核方案</span><strong>将按以下标准进行综合审查</strong></div>
              <b>可继续对话调整</b>
            </div>
            <p>{[
              intakeCriteria.party_role === "party_a" ? "我方为甲方/采购方" : intakeCriteria.party_role === "party_b" ? "我方为乙方/供应方" : intakeCriteria.party_role === "other" ? `我方角色：${intakeCriteria.other_party_role || "待补充"}` : "我方身份待确认",
              intakeCriteria.business_context && `业务目标：${intakeCriteria.business_context}`,
              intakeCriteria.focus_areas.length && `重点：${intakeCriteria.focus_areas.join("、")}`,
              intakeCriteria.non_negotiables && `底线：${intakeCriteria.non_negotiables}`,
            ].filter(Boolean).join("；")}</p>
            <small>这些信息只作为审查立场与谈判偏好，不会被视为合同中已经存在的约定。</small>
            <div className="legal-chat-plan-action">
              <button className="primary-button legal-chat-review-start" type="button" disabled={isLoading || isIntakeChatLoading || !deepReviewSettings.party_role} onClick={() => void runDeepReview()}>
                {isLoading ? "正在进行综合审查…" : "按当前方案开始审查"}
              </button>
            </div>
          </article>
        ) : null}
      </div>

      <div className="legal-chat-dock">
        {intakeChatWarning ? <p className="legal-chat-notice" role="status">{intakeChatWarning}</p> : null}
        {error ? <p className="error-message legal-chat-error">{error}</p> : null}
        <form
          className="legal-chat-composer"
          onSubmit={(event) => {
            if (contractOverview) {
              void sendIntakeChatMessage(event);
              return;
            }
            event.preventDefault();
            if (intakeChatDraft.trim()) void sendIntakeChatMessage();
            else if (!file) fileInputRef.current?.click();
          }}
        >
          <textarea
            value={intakeChatDraft}
            maxLength={2000}
            disabled={isIntakeChatLoading || isLoading}
            onChange={(event) => setIntakeChatDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || !intakeChatDraft.trim()) return;
              event.preventDefault();
              void sendIntakeChatMessage();
            }}
            placeholder={contractOverview ? "告诉 AI 您的立场、业务目标、顾虑，或直接查询相关法规…" : file ? "正在自动读取合同…" : "咨询法规、法条或合同问题；也可通过左侧上传文件"}
          />
          <div className="legal-chat-composer-actions">
            <div className="legal-chat-composer-left-actions">
              <button className="legal-chat-attach" type="button" disabled={isLoading || isIntakeChatLoading} onClick={() => fileInputRef.current?.click()}>
                上传文件
              </button>
              <button
                className="legal-chat-stop"
                type="button"
                disabled={!isLoading && !isIntakeChatLoading && !intakeChatDraft.trim() && intakeMessages[intakeMessages.length - 1]?.role !== "user"}
                onClick={stopIntakeDraft}
                title="终止当前输入或正在生成的回复"
              >
                终止
              </button>
            </div>
            <button
              className="legal-chat-send"
              type="submit"
              title="Enter"
              aria-label="Enter"
              disabled={isLoading || isIntakeChatLoading || !intakeChatDraft.trim()}
            >
              {isIntakeChatLoading ? "…" : "Enter"}
            </button>
          </div>
        </form>
        <div className="legal-chat-dock-footer">
          <span>支持 DOCX / PDF，最大 10MB · 合同内容仅用于本次审查</span>
        </div>
      </div>
    </section>
  );

  return (
    <main className={`app-shell ${review ? "app-shell-review" : "app-shell-chat"}`}>
      <div className={`workbench-shell${!review ? " workbench-shell-chat" : ""}`}>
        <div className="workbench-main">
          <input
            ref={fileInputRef}
            className="hidden-file-input"
            type="file"
            accept=".docx,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf"
            onChange={handleFileChange}
          />

          {!review ? renderIntakeWorkspace() : (
        <section
          className={`workspace robin-review-workspace${isSidebarCollapsed ? " workspace-collapsed" : ""}`}
          aria-busy={isLoading}
          style={readerPanelHeight && !isSidebarCollapsed ? { "--review-panel-height": `${readerPanelHeight}px` } as CSSProperties : undefined}
        >
          <section className="reader-panel robin-document-panel" ref={readerPanelRef}>
            <div className="compact-document-bar">
              <div className="document-info">
                <span className="document-icon" aria-hidden="true">
                  📄
                </span>
                <div>
                  <span className="document-context-label">当前修订文档</span>
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
                  {isLoading ? "正在读取合同概览…" : "重新确认审查诉求"}
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
            <section className="editor-panel editor-panel-promoted" aria-label="合同正文编辑">
              <div className="editor-heading">
                <div>
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
                  正在完成综合审查，正文修改与最终导出将在结果生成后开放。
                </div>
              ) : null}

              <div className="editor-toolbar" role="toolbar" aria-label="正文格式工具">
                <button type="button" title="加粗" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleBold().run()}><strong>B</strong></button>
                <button type="button" title="斜体" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleItalic().run()}><em>I</em></button>
                <button type="button" title="下划线" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleUnderline().run()}><u>U</u></button>
                <button type="button" className="highlight-tool" title="黄色高亮" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleHighlight({ color: "#fff19a" }).run()}>A</button>
                <button type="button" className="text-color-tool" title="绿色文字" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().setColor("#146b49").run()}>A</button>
                <button type="button" className="clear-format-tool" title="清除文字格式" onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().unsetAllMarks().run()}>清除格式</button>
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
                  {isExporting ? "导出中" : "导出 Word 审阅版"}
                </button>
              </div>
            </section>
          </section>

          <aside className={`review-sidebar robin-review-sidebar${isSidebarCollapsed ? " review-sidebar-collapsed" : ""}`}>
            <section className="result-panel robin-result-panel">
              <div className="result-header">
                <div className="result-header-title-row">
                  <div>
                    <span className="result-context-label">智能修订</span>
                    <div className="result-title-line">
                      <h2>审查结果</h2>
                      <span className="result-total-count">{totalRisks} 项</span>
                    </div>
                    {review.contract_type ? <p className="result-subtitle">合同类型：{review.contract_type}</p> : null}
                  </div>
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
                {unlocatableRisks.length ? (
                  <section className="unlocatable-risk-panel" aria-label="未定位到原文的风险项">
                    <div>
                      <strong>{unlocatableRisks.length} 项建议未定位到合同原文</strong>
                      <span>为避免替换错误，系统不会自动改写这些条款。</span>
                    </div>
                    <p>请打开对应风险卡，核对原合同后手动编辑；可复制建议文本，但“已接受修改”只统计实际写入正文的内容。</p>
                  </section>
                ) : null}

                <div className="risk-list-heading">
                  <div>
                    <strong>智能修订建议</strong>
                    <span>保留全部风险原文、分析依据与修改建议</span>
                  </div>
                  <b>{filteredRisks.length}</b>
                </div>

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
                      const appliedModification = modifications.find((item) => isRiskModification(item, risk, riskKey));
                      const accepted = Boolean(appliedModification);
                      const originalLocated = !isMissingClause(risk.original_text) && Boolean(findUniqueExactMatch(editorText, risk.original_text));
                      // Once a verified risk has been written into the
                      // contract, its source text is intentionally replaced
                      // by the revision. It must not be shown again as a
                      // false "location failed" candidate panel.
                      const needsManualOriginalLocation = !accepted && !isMissingClause(risk.original_text) && !originalLocated;
                      const locationCandidates = needsManualOriginalLocation ? findRiskLocationCandidates(editorText, risk) : [];
                      const selectedLocation = selectedRiskLocations[riskKey];
                      const canApplyAtSelectedLocation = Boolean(selectedLocation?.exactOriginal);
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
                                  {accepted ? "已自动修改" : "待处理"}
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
                                {needsManualOriginalLocation ? "定位失败" : "定位"}
                              </button>
                              <button
                                className={`quote-button${isMissingClause(risk.original_text) ? " quote-append" : ""}${accepted ? " quote-button-done" : ""}`}
                                type="button"
                                disabled={accepted || reviewStage !== "modification" || (needsManualOriginalLocation && !canApplyAtSelectedLocation)}
                                onClick={() => applySuggestion(risk, riskKey)}
                              >
                                {accepted
                                  ? "已处理"
                                  : reviewStage !== "modification"
                                    ? "深度审查后可修改"
                                    : needsManualOriginalLocation
                                      ? canApplyAtSelectedLocation
                                        ? "在选中处引用"
                                        : "确认定位后修改"
                                      : isMissingClause(risk.original_text)
                                        ? "由我补充"
                                        : "引用修改"}
                              </button>
                              {accepted && appliedModification ? (
                                <button className="secondary-button inline-button" type="button" onClick={() => undoRiskModification(risk, riskKey)}>
                                  撤销本项
                                </button>
                              ) : null}
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

                          {needsManualOriginalLocation ? (
                            <div className="manual-location-panel">
                              <strong>{locationCandidates.length ? "请确认对应的合同段落" : "未能找到可靠的候选段落"}</strong>
                              <p>{locationCandidates.length ? "系统已按原文、邻近锚点及文字相似度找出候选段落。只有包含完整引文的候选段落可自动引用修改；其他候选仅用于帮助您找到原文。" : "模型返回的引文与当前合同文字差异较大。为避免误删或误改，请在左侧核对原合同后手动编辑。"}</p>
                              {locationCandidates.length ? (
                                <div className="location-candidate-list">
                                  {locationCandidates.map((candidate, index) => (
                                    <button
                                      className={`location-candidate${selectedLocation?.paragraphIndex === candidate.paragraphIndex && selectedLocation.selectionFrom === candidate.selectionFrom ? " location-candidate-selected" : ""}`}
                                      type="button"
                                      key={`${candidate.paragraphIndex}-${candidate.selectionFrom}`}
                                      onClick={() => selectRiskLocation(risk, riskKey, candidate)}
                                    >
                                      <span>候选 {index + 1} · {candidate.reason === "exact" ? "引文完全匹配" : candidate.reason === "anchor" ? "邻近条款匹配" : "文字相似匹配"}</span>
                                      <small>{candidate.paragraph.length > 92 ? `${candidate.paragraph.slice(0, 92)}...` : candidate.paragraph}</small>
                                    </button>
                                  ))}
                                </div>
                              ) : null}
                              <button className="secondary-button inline-button" type="button" onClick={() => void copySuggestionToClipboard(risk)}>
                                复制修改建议
                              </button>
                            </div>
                          ) : null}

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

                {reviewStage === "modification" && preflightChecks.length ? (
                  <details className="preflight-panel" aria-label="基础质量与合同框架检查">
                    <summary className="preflight-heading">
                      <div>
                        <strong>基础质量与合同框架</strong>
                        <span>文字、标点和框架检查；点击展开查看明细。</span>
                      </div>
                      <b className={preflightWarnings.length ? "preflight-count-warning" : "preflight-count-passed"}>
                        {preflightWarnings.length ? `需核对 ${preflightWarnings.length} 项` : "检查通过"}
                      </b>
                    </summary>
                    <div className="preflight-list">
                      {preflightChecks.map((check, index) => {
                        const checkKey = `${check.category}-${check.title}-${index}`;
                        const decision = preflightDecisions[checkKey];
                        const needsDecision = check.status === "warning" && !check.auto_fixable;
                        return (
                          <article className={`preflight-row preflight-row-${check.status}`} key={checkKey}>
                            <div className="preflight-row-heading">
                              <span className={`preflight-category preflight-category-${check.category}`}>
                                {check.category === "structure" ? "框架" : check.category === "scope" ? "范围" : check.category === "punctuation" ? "标点" : "文字"}
                              </span>
                              <strong>{check.title}</strong>
                              <b>
                                {check.status === "passed"
                                  ? "已检查"
                                  : check.auto_fixable
                                    ? "已自动修正"
                                    : decision === "confirmed"
                                      ? "已确认"
                                      : decision === "deferred"
                                        ? "暂不处理"
                                        : "待人工确认"}
                              </b>
                            </div>
                            {check.evidence ? <p>{check.evidence}</p> : null}
                            {check.suggestion ? <small>建议：{check.suggestion}</small> : null}
                            {needsDecision ? (
                              <div className="preflight-quality-actions">
                                <small>此项不会自动改写合同，请核对原件后选择处理方式。</small>
                                <div>
                                  <button
                                    className={decision === "confirmed" ? "preflight-quality-active" : ""}
                                    type="button"
                                    onClick={() => setPreflightDecision(checkKey, "confirmed", check.title)}
                                  >
                                    确认已核对
                                  </button>
                                  <button
                                    className={decision === "deferred" ? "preflight-quality-active" : ""}
                                    type="button"
                                    onClick={() => setPreflightDecision(checkKey, "deferred", check.title)}
                                  >
                                    暂不处理
                                  </button>
                                </div>
                              </div>
                            ) : null}
                          </article>
                        );
                      })}
                    </div>
                  </details>
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
              </div>
            </section>
          </aside>
        </section>
          )}
        </div>
      </div>
    </main>
  );
}
