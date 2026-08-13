import html

from app.schemas.review import ReviewResponse


def _escape(value: object) -> str:
    return html.escape(str(value))


def render_review_report(review: ReviewResponse) -> str:
    rows = []
    for risk in review.risks:
        references = []
        for reference in risk.law_references:
            label = _escape(reference.label)
            if reference.official_url:
                url = html.escape(reference.official_url, quote=True)
                references.append(f'<a href="{url}">{label}</a>')
            else:
                references.append(label)
        rows.append(
            "<tr>"
            f"<td>{_escape(risk.item)}</td>"
            f"<td class=level-{_escape(risk.level)}>{_escape(risk.level)}</td>"
            f"<td>{_escape(risk.evidence_status)}</td>"
            f"<td class=quote>{_escape(risk.original_text)}</td>"
            f"<td>{_escape(risk.risk)}</td>"
            f"<td>{_escape(risk.suggestion)}</td>"
            f"<td>{'<br>'.join(references) or '未绑定权威法规'}</td>"
            "</tr>"
        )

    warnings = "".join(f"<li>{_escape(item)}</li>" for item in review.warnings)
    scope = "、".join(_escape(item) for item in review.review_scope) or "未记录"
    coverage = "、".join(
        f"{_escape(item.topic)}（{_escape(item.status)}）"
        for item in review.coverage
    ) or "未记录"
    warning_block = warnings or "<li>无</li>"
    risk_rows = "".join(rows) or '<tr><td colspan="7" class="empty">未发现可直接展示的风险项</td></tr>'
    preflight_rows = "".join(
        "<tr>"
        f"<td>{_escape(check.category)}</td>"
        f"<td>{_escape(check.title)}</td>"
        f"<td>{_escape(check.status)}</td>"
        f"<td>{_escape(check.evidence)}</td>"
        f"<td>{_escape(check.suggestion)}</td>"
        "</tr>"
        for check in review.preflight_checks
    ) or '<tr><td colspan="5" class="empty">本次未启用基础质量与合同框架检查</td></tr>'

    deep_review_block = ""
    if review.deep_review:
        facts = "".join(
            "<tr>"
            f"<td>{_escape(fact.item)}</td>"
            f"<td>{_escape(fact.contract_term)}</td>"
            f"<td>{_escape(fact.conclusion)}</td>"
            "</tr>"
            for fact in review.deep_review.key_facts
        ) or '<tr><td colspan="3" class="empty">未返回关键条款摘要</td></tr>'
        missing = "".join(f"<li>{_escape(item)}</li>" for item in review.deep_review.missing_clauses) or "<li>未识别</li>"
        negotiations = "".join(
            "<tr>"
            f"<td>{_escape(item.topic)}</td>"
            f"<td>{_escape(item.target)}</td>"
            f"<td>{_escape(item.minimum_acceptable)}</td>"
            f"<td>{_escape(item.owner)}</td>"
            "</tr>"
            for item in review.deep_review.negotiation_items
        ) or '<tr><td colspan="4" class="empty">未返回谈判事项</td></tr>'
        questions = "".join(f"<li>{_escape(item)}</li>" for item in review.deep_review.clarification_questions) or "<li>无</li>"
        deep_review_block = f"""
  <h2>深度商业与谈判审查</h2>
  <div class="summary"><strong>结论：{_escape(review.deep_review.overall_conclusion)}</strong><br>{_escape(review.deep_review.executive_summary)}<br><small>{_escape(review.deep_review.settings_note)}</small></div>
  <h2>关键条款与结论</h2>
  <table><thead><tr><th>事项</th><th>合同约定</th><th>审查结论</th></tr></thead><tbody>{facts}</tbody></table>
  <h2>需补充条款</h2><ul class="warning">{missing}</ul>
  <h2>谈判清单</h2>
  <table><thead><tr><th>事项</th><th>我方目标</th><th>最低可接受条件</th><th>责任方</th></tr></thead><tbody>{negotiations}</tbody></table>
  <h2>待业务确认</h2><ul class="warning">{questions}</ul>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>合同审查报告</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 18px auto;
      max-width: 1380px;
      padding: 0 18px;
      color: #17251e;
      background: #fff;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      font-size: 13px;
      line-height: 1.45;
    }}
    h1 {{ margin: 0 0 6px; font-size: 24px; line-height: 1.2; }}
    h2 {{ margin: 14px 0 5px; font-size: 16px; line-height: 1.25; }}
    p {{ margin: 3px 0; }}
    .meta {{ color: #5c6d64; }}
    .summary {{ margin: 4px 0 8px; padding: 7px 9px; background: #f3f8f5; border-left: 3px solid #146b49; }}
    .warning {{ margin: 3px 0 8px; padding: 6px 10px 6px 28px; color: #934b20; background: #fff8ef; }}
    .warning li {{ margin: 1px 0; }}
    .scope {{ display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 3px 0 8px; color: #43584d; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 5px; }}
    th, td {{ border: 1px solid #ccd8d0; padding: 5px 6px; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #edf6f0; font-weight: 700; text-align: left; }}
    th:nth-child(1) {{ width: 10%; }}
    th:nth-child(2) {{ width: 6%; }}
    th:nth-child(3) {{ width: 9%; }}
    th:nth-child(4) {{ width: 19%; }}
    th:nth-child(5) {{ width: 19%; }}
    th:nth-child(6) {{ width: 22%; }}
    th:nth-child(7) {{ width: 15%; }}
    .quote {{ white-space: pre-wrap; }}
    .empty {{ text-align: center; color: #6a7b72; padding: 10px; }}
    a {{ color: #126b4a; }}
    @media print {{
      body {{ margin: 8mm; padding: 0; font-size: 10px; }}
      h2 {{ break-after: avoid; }}
      tr {{ break-inside: avoid; }}
      .summary, .warning {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>
  <h1>合同审查报告</h1>
  <p class="meta">文件：{_escape(review.filename)}　|　状态：{_escape(review.review_status)}　|　风险数：{len(review.risks)}　|　耗时：{review.review_duration_ms or '-'} ms</p>
  <h2>审查说明</h2>
  <div class="summary">{_escape(review.review_summary) or '未生成审查说明，请人工复核。'}</div>
  <h2>审查范围</h2>
  <div class="scope"><span><strong>检查项：</strong>{scope}</span><span><strong>覆盖情况：</strong>{coverage}</span></div>
  <h2>基础质量与合同框架预检</h2>
  <table>
    <thead><tr><th>类别</th><th>检查项</th><th>状态</th><th>定位依据</th><th>处理建议</th></tr></thead>
    <tbody>{preflight_rows}</tbody>
  </table>
  <h2>人工复核提示</h2>
  <ul class="warning">{warning_block}</ul>
  <h2>风险明细</h2>
  <table>
    <thead><tr><th>检查项</th><th>等级</th><th>证据状态</th><th>合同原文</th><th>风险</th><th>建议</th><th>法规依据</th></tr></thead>
    <tbody>{risk_rows}</tbody>
  </table>
  {deep_review_block}
</body>
</html>"""
