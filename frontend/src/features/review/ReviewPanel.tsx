import type { DeepReviewOutput, ReviewStage } from "../../domain/reviewTypes";

export function ReviewPanel({ deepReview, reviewStage }: { deepReview?: DeepReviewOutput | null; reviewStage: ReviewStage }) {
  if (reviewStage !== "modification" || !deepReview) return null;
  return (
    <section className="deep-review-result" aria-label="深度审查结论">
      <div className="deep-review-heading"><div><strong>深度审查结论：{deepReview.overall_conclusion}</strong><span>{deepReview.settings_note}</span></div><b>已完成</b></div>
      <p>{deepReview.executive_summary}</p>
      {deepReview.key_facts.length ? <details open><summary>关键条款与结论</summary><div className="deep-result-list">{deepReview.key_facts.map((fact, index) => <article key={`${fact.item}-${index}`}><b>{fact.item}</b><span>{fact.contract_term}</span><small>{fact.conclusion}</small></article>)}</div></details> : null}
      {deepReview.missing_clauses.length ? <details><summary>需补充的条款</summary><ul>{deepReview.missing_clauses.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
      {deepReview.negotiation_items.length ? <details><summary>谈判清单</summary><div className="deep-result-list">{deepReview.negotiation_items.map((item, index) => <article key={`${item.topic}-${index}`}><b>{item.topic} · {item.owner}</b><span>目标：{item.target}</span><small>底线：{item.minimum_acceptable}</small></article>)}</div></details> : null}
      {deepReview.clarification_questions.length ? <details><summary>待业务确认</summary><ul>{deepReview.clarification_questions.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
    </section>
  );
}
