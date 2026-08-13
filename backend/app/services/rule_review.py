from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.review import ReviewCoverage, ReviewRisk
from app.services.document_preflight import PREFLIGHT_SCOPE


@dataclass(frozen=True)
class RuleTopic:
    name: str
    keywords: tuple[str, ...]
    level: str
    suggestion: str
    laws: tuple[str, ...] = ()


RULE_TOPICS = (
    RuleTopic(
        "主体与签约权限",
        ("甲方", "乙方", "统一社会信用代码", "法定代表人", "授权代表", "签署", "盖章"),
        "high",
        "核对合同主体全称、统一社会信用代码、签约代表权限、盖章主体及履行主体；补充授权和主体变更责任。",
    ),
    RuleTopic(
        "合同成立与效力",
        ("成立", "生效", "签署后生效", "盖章后生效", "审批", "备案", "前提条件"),
        "high",
        "明确合同成立、生效时间及生效条件，核对审批、备案、许可等前置条件和无效风险。",
    ),
    RuleTopic(
        "标的与价格",
        ("合同标的", "产品名称", "服务内容", "规格", "型号", "数量", "单价", "总价", "含税"),
        "high",
        "明确标的、规格、数量、质量要求、计价方式、税费承担和价格调整机制，避免交易对象和金额不确定。",
    ),
    RuleTopic(
        "付款与发票",
        ("付款", "支付", "价款", "货款", "结算", "账期", "发票", "开票"),
        "high",
        "补充付款节点、付款条件、发票类型及逾期付款责任。",
    ),
    RuleTopic(
        "交付与验收",
        ("验收", "交付", "交货", "上线", "交接"),
        "medium",
        "补充交付物、交付时间、验收标准、验收期限及逾期未验收的处理方式。",
    ),
    RuleTopic(
        "质量与售后",
        ("质量", "质保", "保修", "售后", "维护", "维修", "服务等级", "SLA", "响应时间"),
        "medium",
        "明确质量标准、质保期限、售后响应时间、维修更换、服务等级和未达标补救措施。",
    ),
    RuleTopic(
        "违约与责任",
        ("违约", "赔偿", "损失", "违约责任", "责任承担", "责任限制"),
        "high",
        "补充违约情形、违约金或损害赔偿、责任上限及责任承担方式。",
    ),
    RuleTopic(
        "解除与终止",
        ("解除", "终止", "续约", "自动续期", "提前终止", "终止后", "退出"),
        "high",
        "明确合同期限、续约、单方解除、重大违约解除、终止通知、结算、资料返还和过渡义务。",
    ),
    RuleTopic(
        "知识产权",
        ("知识产权", "著作权", "专利", "商标", "软件", "成果归属", "源代码"),
        "medium",
        "明确既有知识产权、新生成成果及相关源代码、许可和使用权的归属。",
    ),
    RuleTopic(
        "保密与数据",
        ("保密", "秘密信息", "商业秘密", "个人信息", "数据安全", "数据处理", "返还", "销毁"),
        "high",
        "明确保密信息范围、例外、保密期限、数据处理权限、个人信息保护、返还销毁和泄露处置。",
    ),
    RuleTopic(
        "合规与许可",
        ("合规", "许可证", "资质", "监管", "反商业贿赂", "制裁", "出口管制", "适用法律"),
        "high",
        "核对双方资质、行业许可、监管要求、反商业贿赂、制裁及出口管制等强制性合规义务。",
    ),
    RuleTopic(
        "通知与送达",
        ("通知", "送达", "联系人", "电子邮箱", "通讯地址", "变更地址"),
        "medium",
        "明确通知方式、联系人、地址和邮箱，约定变更通知、送达生效及未及时更新的责任。",
    ),
    RuleTopic(
        "争议解决",
        ("争议", "仲裁", "诉讼", "管辖", "法院", "仲裁机构", "争议解决"),
        "high",
        "明确适用法律、争议解决方式、管辖法院或仲裁机构、地点和语言，避免管辖约定无效或冲突。",
    ),
    RuleTopic(
        "附件与文本一致性",
        ("附件", "订单", "报价单", "技术协议", "补充协议", "优先顺序", "完整协议", "冲突"),
        "medium",
        "核对正文、附件、订单和报价单的效力顺序、交叉引用、金额日期及版本，补充冲突解决规则。",
    ),
)


def _evidence(text: str, keyword: str) -> str:
    index = text.find(keyword)
    if index < 0:
        return ""
    start = max(0, index - 40)
    end = min(len(text), index + len(keyword) + 80)
    return text[start:end].replace("\n", " ").strip()


def run_rule_fallback(
    contract_text: str,
    selected_topics: list[str] | None = None,
) -> tuple[list[ReviewRisk], list[ReviewCoverage]]:
    """Check high-value topics deterministically when model output is incomplete."""
    risks: list[ReviewRisk] = []
    coverage: list[ReviewCoverage] = []
    selected = set(
        [topic.name for topic in RULE_TOPICS]
        if selected_topics is None
        else selected_topics
    )

    if PREFLIGHT_SCOPE in selected:
        coverage.append(
            ReviewCoverage(
                topic=PREFLIGHT_SCOPE,
                status="checked",
                evidence="已完成基础质量、标点和合同框架检查。",
                method="rule",
            )
        )

    for topic in RULE_TOPICS:
        if topic.name not in selected:
            continue
        if topic.name == "主体与签约权限":
            has_chinese_parties = "甲方" in contract_text and "乙方" in contract_text
            has_english_parties = bool(
                re.search(r"\b(?:party\s*)?a\b", contract_text, re.I)
                and re.search(r"\b(?:party\s*)?b\b", contract_text, re.I)
            )
            matched_keyword = "双方主体角色" if has_chinese_parties or has_english_parties else None
        else:
            matched_keyword = next((keyword for keyword in topic.keywords if keyword in contract_text), None)
        if matched_keyword:
            coverage.append(
                ReviewCoverage(
                    topic=topic.name,
                    status="checked",
                    evidence=_evidence(contract_text, matched_keyword),
                    method="rule",
                )
            )
            continue

        coverage.append(ReviewCoverage(topic=topic.name, status="missing", method="rule"))
        risks.append(
            ReviewRisk(
                item=topic.name,
                level=topic.level,
                original_text="【缺失该约定】",
                risk=f"合同正文未检出与“{topic.name}”相关的关键约定，需人工确认是否确实缺失。",
                suggestion=topic.suggestion,
                laws=list(topic.laws),
                source="rule",
            )
        )

    return risks, coverage
