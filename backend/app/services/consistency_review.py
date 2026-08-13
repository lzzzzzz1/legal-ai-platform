from __future__ import annotations

import re

from app.schemas.review import ReviewConsistencyCheck


_AMOUNT_RE = re.compile(r"(?:人民币|¥|￥)\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?")
_ATTACHMENT_RE = re.compile(r"附件\s*[一二三四五六七八九十0-9]")
_DATE_RE = re.compile(r"(?:20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?|20\d{2}-\d{1,2}-\d{1,2})")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:1[3-9]\d{9}|(?:0\d{2,3}-?)?\d{7,8})")
_DISPUTE_VENUE_RE = re.compile(r"(?:仲裁委员会|人民法院|仲裁地|管辖法院|有管辖权的法院)")
_DUE_PERIOD_RE = re.compile(r"(?:\d+\s*(?:个)?(?:工作日|自然日|日|天)|[一二三四五六七八九十]+\s*(?:个)?(?:工作日|自然日|日|天))")


def _evidence(text: str, needle: str) -> str:
    index = text.find(needle)
    if index < 0:
        return ""
    return " ".join(text[max(0, index - 35): index + len(needle) + 70].split())


def run_consistency_checks(contract_text: str, selected_topics: list[str]) -> list[ReviewConsistencyCheck]:
    """Run conservative, explainable checks that do not replace legal judgment."""
    checks: list[ReviewConsistencyCheck] = []
    selected = set(selected_topics)

    if "主体与签约权限" in selected:
        has_party_a = "甲方" in contract_text or re.search(r"\bA\s*(?:Party)?\b", contract_text, re.I)
        has_party_b = "乙方" in contract_text or re.search(r"\bB\s*(?:Party)?\b", contract_text, re.I)
        if has_party_a and has_party_b:
            checks.append(ReviewConsistencyCheck(
                check="合同主体角色完整性", status="checked", evidence="已检出甲方/乙方或 A/B 角色标识",
                note="仍需核对名称、统一社会信用代码和签署权限。",
            ))
        else:
            missing = "甲方" if not has_party_a else "乙方"
            checks.append(ReviewConsistencyCheck(
                check="合同主体角色完整性", status="warning", evidence="",
                note=f"未检出{missing}角色标识，需人工确认合同主体是否完整。",
            ))

    if selected & {"标的与价格", "付款与发票"}:
        amounts = _AMOUNT_RE.findall(contract_text)
        if ("总价" in contract_text or "合计" in contract_text) and not amounts:
            checks.append(ReviewConsistencyCheck(
                check="金额与总价一致性", status="warning", evidence="总价/合计附近未检出金额",
                note="合同提及总价或合计，但未检出可识别的人民币金额。",
            ))
        elif amounts:
            checks.append(ReviewConsistencyCheck(
                check="金额与总价一致性", status="checked", evidence="、".join(dict.fromkeys(amounts[:8])),
                note="已检出金额表达；不代表分项金额与总价的算术关系已经确认。",
            ))

        currencies = {currency for currency in ("人民币", "美元", "欧元", "港币") if currency in contract_text}
        if len(currencies) > 1:
            checks.append(ReviewConsistencyCheck(
                check="币种一致性", status="warning", evidence="、".join(sorted(currencies)),
                note="合同出现多个币种，需人工确认是否存在币种混用或汇率约定缺失。",
            ))

        if "付款" in contract_text or "支付" in contract_text:
            payment_excerpt = _evidence(contract_text, "付款") or _evidence(contract_text, "支付")
            if not _DUE_PERIOD_RE.search(payment_excerpt):
                checks.append(ReviewConsistencyCheck(
                    check="付款期限可执行性", status="warning", evidence=payment_excerpt,
                    note="已提及付款或支付，但相邻文本未检出明确期限；需确认付款触发条件和期限。",
                ))
            else:
                checks.append(ReviewConsistencyCheck(
                    check="付款期限可执行性", status="checked", evidence=payment_excerpt,
                    note="已检出付款期限表达；仍需核对付款条件、金额和发票要求是否对应。",
                ))

    if selected & {"合同成立与效力", "解除与终止"}:
        dates = list(dict.fromkeys(_DATE_RE.findall(contract_text)))
        if ("生效" in contract_text or "签订" in contract_text) and not dates:
            checks.append(ReviewConsistencyCheck(
                check="生效日期可识别性", status="warning", evidence=_evidence(contract_text, "生效") or _evidence(contract_text, "签订"),
                note="合同提及生效或签订，但未检出明确日期；需人工确认生效条件和期限起算点。",
            ))
        elif dates:
            checks.append(ReviewConsistencyCheck(
                check="生效日期可识别性", status="checked", evidence="、".join(dates[:6]),
                note="已检出日期表达；多个日期可能分别对应签订、生效、履行或终止，需结合条款确认。",
            ))

    if "通知与送达" in selected:
        has_notice_clause = "通知" in contract_text or "送达" in contract_text
        has_contact = bool(_EMAIL_RE.search(contract_text) or _PHONE_RE.search(contract_text) or "地址" in contract_text)
        if has_notice_clause and has_contact:
            checks.append(ReviewConsistencyCheck(
                check="通知信息可送达性", status="checked", evidence=_evidence(contract_text, "通知") or _evidence(contract_text, "送达"),
                note="已检出通知/送达条款和至少一种联系信息；仍需核对收件人、地址或邮箱是否完整。",
            ))
        elif has_notice_clause:
            checks.append(ReviewConsistencyCheck(
                check="通知信息可送达性", status="warning", evidence=_evidence(contract_text, "通知") or _evidence(contract_text, "送达"),
                note="已提及通知或送达，但未检出地址、邮箱或电话等联系信息。",
            ))

    if "争议解决" in selected and ("争议" in contract_text or "仲裁" in contract_text or "诉讼" in contract_text):
        dispute_evidence = _evidence(contract_text, "争议") or _evidence(contract_text, "仲裁")
        if _DISPUTE_VENUE_RE.search(contract_text):
            checks.append(ReviewConsistencyCheck(
                check="争议解决路径明确性", status="checked", evidence=dispute_evidence,
                note="已检出仲裁机构或法院管辖表述；仍需核对适用法律、地点和排他性约定。",
            ))
        else:
            checks.append(ReviewConsistencyCheck(
                check="争议解决路径明确性", status="warning", evidence=dispute_evidence,
                note="合同提及争议处理，但未检出明确仲裁机构或法院管辖表述。",
            ))

    if "附件与文本一致性" in selected and "附件" in contract_text:
        if not _ATTACHMENT_RE.search(contract_text):
            checks.append(ReviewConsistencyCheck(
                check="附件引用一致性", status="warning", evidence=_evidence(contract_text, "附件"),
                note="正文提及附件，但未检出带编号的附件引用，需核对附件是否完整。",
            ))
        else:
            checks.append(ReviewConsistencyCheck(
                check="附件引用一致性", status="checked", evidence="已检出带编号的附件引用",
                note="仍需打开附件核对版本、金额和优先顺序。",
            ))

    return checks
