import type {
  ContractOverviewResponse,
  IntakeChatMessage,
  IntakeReviewCriteria,
  IntakeChatResponse,
} from "../domain/reviewTypes";
import { apiHeaders, getApiErrorMessage } from "./client";

export { apiHeaders } from "./client";

const unsupportedEditorCharacters = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

const EMPTY_INTAKE_CRITERIA: IntakeReviewCriteria = {
  party_role: null,
  other_party_role: "",
  deal_priorities: [],
  focus_areas: [],
  review_style: "protective",
  business_context: "",
  non_negotiables: "",
  special_requirements: [],
  additional_notes: [],
};
export async function getContractOverview(file: File): Promise<ContractOverviewResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/api/overview", {
    method: "POST",
    headers: apiHeaders(),
    body: formData
  });
  if (!response.ok) {
    throw new Error(await getApiErrorMessage(response, `Contract overview request failed with status ${response.status}.`));
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

export function normalizeIntakeCriteria(value: unknown): IntakeReviewCriteria {
  if (!value || typeof value !== "object") return { ...EMPTY_INTAKE_CRITERIA };
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

export async function continueIntakeChat(
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
    throw new Error(await getApiErrorMessage(response, `法务助手对话请求失败（${response.status}）。`));
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
