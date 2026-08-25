"""Fast, conservative checks that run before the detailed contract review.

This is not a Chinese spell-checker or a legal opinion.  It only identifies
objective formatting/typing signals and the most basic contract-frame markers,
so a user can correct a draft before the substantive review begins.
"""

from __future__ import annotations

import re

from app.schemas.review import DocumentPreflightCheck


PREFLIGHT_SCOPE = "基础质量与合同框架"
MAX_FINDINGS = 12

# The first review only determines whether a contract has a recognisable place
# for each essential module.  It deliberately does not decide whether a term
# favours either party; that is reserved for the deep commercial review.
FRAMEWORK_MODULES: tuple[tuple[str, str], ...] = (
    ("主体与签约权限", r"甲方|乙方|party\s*[ab]|签约|授权|法定代表人"),
    ("合同成立与效力", r"生效|成立|签署.*生效|effective\s+date"),
    ("标的与价格", r"标的|服务范围|产品|价款|合同金额|报价|人民币|¥|￥"),
    ("付款与发票", r"付款|支付|发票|开票|税率"),
    ("交付与验收", r"交付|验收|上线|交货|交付物"),
    ("质量与售后", r"质量|售后|保修|维护|服务等级|SLA"),
    ("违约与责任", r"违约|违约金|赔偿|责任"),
    ("解除与终止", r"解除|终止|退出|到期"),
    ("知识产权", r"知识产权|著作权|专利|商标|源代码"),
    ("保密与数据", r"保密|数据|个人信息|隐私"),
    ("合规与许可", r"合规|许可|资质|反商业贿赂|制裁"),
    ("通知与送达", r"通知|送达|通讯地址|电子邮件|邮箱"),
    ("争议解决", r"争议|仲裁|管辖|法院"),
    ("附件与文本一致性", r"附件|补充协议|组成部分|文本"),
)


def _excerpt(text: str, start: int, length: int) -> str:
    left = max(0, start - 24)
    right = min(len(text), start + length + 42)
    return text[left:right].replace("\n", " ").strip()


def _passed(title: str, evidence: str) -> DocumentPreflightCheck:
    return DocumentPreflightCheck(
        category="structure", title=title, status="passed", evidence=evidence
    )


def _warning(
    category: str,
    title: str,
    evidence: str,
    suggestion: str,
    original_text: str | None = None,
    replacement_text: str | None = None,
    auto_fixable: bool = False,
) -> DocumentPreflightCheck:
    return DocumentPreflightCheck(
        category=category,  # type: ignore[arg-type]
        title=title,
        status="warning",
        evidence=evidence,
        suggestion=suggestion,
        original_text=original_text,
        replacement_text=replacement_text,
        auto_fixable=auto_fixable,
    )


def run_document_preflight(contract_text: str) -> list[DocumentPreflightCheck]:
    """Run low-cost checks without using an LLM or external dictionary.

    Findings labelled as typo candidates are deliberately conservative and
    always ask the user to confirm; we never silently change contract text.
    """
    text = contract_text or ""
    checks: list[DocumentPreflightCheck] = []
    opening = text[:1000].replace("\n", " ").strip()

    has_title = bool(re.search(r"(?:合同|协议|agreement)\b", text[:1000], re.IGNORECASE))
    checks.append(
        _passed("合同标题", opening[:120])
        if has_title
        else _warning("structure", "合同标题", "文档首页未识别合同或协议标题。", "在首页补充明确的合同/协议名称和版本信息。")
    )

    has_cn_parties = "甲方" in text and "乙方" in text
    has_en_parties = bool(
        re.search(r"\b(?:party\s*)?a\b", text, re.IGNORECASE)
        and re.search(r"\b(?:party\s*)?b\b", text, re.IGNORECASE)
    )
    checks.append(
        _passed("合同主体标识", "已识别双方主体标识。")
        if has_cn_parties or has_en_parties
        else _warning("structure", "合同主体标识", "未同时识别双方主体标识。", "核对首页或主体条款是否明确列示双方全称及主体角色。")
    )

    has_clause_structure = bool(
        re.search(r"(?m)^\s*(?:第[一二三四五六七八九十百千万零〇0-9]+条|\d+(?:\.\d+){0,3}[、.．])", text)
    )
    checks.append(
        _passed("正文条款层级", "已识别条款编号或分级结构。")
        if has_clause_structure
        else _warning("structure", "正文条款层级", "未识别常见的条款编号或分级结构。", "建议使用连续的条款标题或编号，便于定位、修改和履行。")
    )

    has_signature = bool(re.search(r"签字|签署|盖章|授权代表|法定代表人|signature|signed by", text, re.IGNORECASE))
    checks.append(
        _passed("签署区提示", "已识别签署、盖章或授权代表信息。")
        if has_signature
        else _warning("structure", "签署区提示", "正文中未识别签署或盖章信息。", "请核对完整文件（含尾页）是否包含签署、盖章及日期栏。")
    )

    for title, pattern in FRAMEWORK_MODULES:
        match = re.search(pattern, text, re.IGNORECASE)
        checks.append(
            DocumentPreflightCheck(
                category="scope",
                title=title,
                status="passed",
                evidence=_excerpt(text, match.start(), len(match.group())) if match else "",
                suggestion="",
            )
            if match
            else _warning(
                "scope",
                title,
                "未识别与该合同模块对应的基础约定。",
                f"如本次交易需要“{title}”模块，请由业务或法务确认后在正文中补充；系统不会自动新增该模块。",
            )
        )

    findings: list[DocumentPreflightCheck] = []
    seen: set[tuple[str, str, int]] = set()

    def add_match(
        category: str,
        title: str,
        match: re.Match[str],
        suggestion: str,
        replacement_text: str | None = None,
        auto_fixable: bool = False,
    ) -> None:
        key = (category, title, match.start())
        if key in seen or len(findings) >= MAX_FINDINGS:
            return
        seen.add(key)
        findings.append(
            _warning(
                category,
                title,
                _excerpt(text, match.start(), len(match.group())),
                suggestion,
                original_text=match.group(),
                replacement_text=replacement_text,
                auto_fixable=auto_fixable,
            )
        )

    # Repeated commas/stops are unambiguous drafting defects.  Do not flag the
    # normal Chinese ellipsis (……) or a normal English ellipsis (...).
    for match in re.finditer(r"([，；：！？、])\1+", text):
        add_match(
            "punctuation", "重复中文标点", match,
            "已自动删除重复标点，仅保留一个与句意匹配的标点符号。",
            match.group()[0], True,
        )
    for match in re.finditer(r"([,;:!?])\1+", text):
        add_match(
            "punctuation", "重复英文标点", match,
            "已自动删除重复标点，仅保留一个与句意匹配的标点符号。",
            match.group()[0], True,
        )
    for match in re.finditer(r"(?<=[\u4e00-\u9fff])[,;:](?=\S)|(?<=\S)[,;:](?=[\u4e00-\u9fff])", text):
        punctuation_map = {",": "，", ";": "；", ":": "："}
        add_match(
            "punctuation", "中英文标点混用", match,
            "已自动统一为中文全角标点；英文条款请保留英文标点。",
            punctuation_map[match.group()], True,
        )

    for match in re.finditer("�", text):
        add_match("typo", "异常替换字符", match, "该字符通常表示解析或编码异常，请回到原文件核对并更正。")
    for match in re.finditer(r"([\u4e00-\u9fff]{2,8})\1", text):
        add_match("typo", "疑似重复输入", match, "请确认相邻重复词组是否为误输入；如非误输入可忽略。")

    known_typos = {
        "签暑": "签署",
        "履行行": "履行",
        "验收收": "验收",
        "仲裁裁": "仲裁",
        "违约金金": "违约金",
    }
    for typo, correction in known_typos.items():
        for match in re.finditer(re.escape(typo), text):
            add_match(
                "typo", "明确错别字", match,
                f"已自动将“{typo}”更正为“{correction}”。",
                correction, True,
            )

    return checks + findings
