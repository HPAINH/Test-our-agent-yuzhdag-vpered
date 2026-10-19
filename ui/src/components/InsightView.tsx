import { useMemo, useState } from "react";
import { api, Claim, fmt, InsightPayload } from "../api";
import { ClaimItem, KindLegend } from "./Claims";
import Chat from "./Chat";
import SourceDrawer from "./SourceDrawer";
import TracePanel from "./TracePanel";

const POL_LABEL = { risk: "Риск", opportunity: "Возможность", mixed: "Требует внимания" } as const;
const REASONS: [string, string][] = [
  ["irrelevant", "Не про нас"],
  ["factual_error", "Ошибка в фактах"],
  ["obvious", "Очевидно"],
  ["no_action", "Нет пользы для решений"],
];

export default function InsightView({ data, onRegenerate }: { data: InsightPayload; onRegenerate?: () => void }) {
  const b = data.insight;
  const s = b.sections;
  const [drawer, setDrawer] = useState<{ id: string; quote?: string | null } | null>(null);
  const [tab, setTab] = useState<"chat" | "trace">("chat");
  const [voted, setVoted] = useState<string | null>(null);
  const claims = useMemo(() => {
    const all: Claim[] = [
      ...s.what_happened, ...s.why_it_matters, ...s.consequences.risks, ...s.consequences.opportunities,
      ...s.consequences.watch_factors, ...s.actions, ...s.bank_view,
    ];
    return Object.fromEntries(all.map((c) => [c.id, c]));
  }, [s]);

  const open = (id: string, quote?: string | null) => {
    api.uiEvent("open_source", id);
    setDrawer({ id, quote });
  };
  const vote = async (v: 1 | -1, reason?: string) => {
    await api.feedback(b.insight_id, v, reason).catch(() => undefined);
    setVoted(v > 0 ? "Спасибо, отметили как полезное" : "Спасибо, учтём при проверке качества");
  };
  const item = (c: Claim) => (
    <ClaimItem key={c.id} c={c} sources={data.sources} claims={claims} onOpen={open}>
      {c.bank_product_title && (
        <div className="offer">
          <span>
            <b>Предложение банка:</b> {c.bank_product_title}
          </span>
          <button className="btn small" onClick={() => alert("В прототипе заявка не отправляется: это смоделированная интеграция с продуктами банка.")}>
            Узнать условия
          </button>
        </div>
      )}
    </ClaimItem>
  );

  const factorNames = b.factors.map((f) => data.factor_names[f] || f);
  const notRelevant = b.status === "not_relevant";

  return (
    <>
      <div className="ins-grid">
        <div>
          <div className="ins-head">
            <div className="meta-row">
              {!notRelevant && <span className={`pol ${b.polarity}`}>{POL_LABEL[b.polarity]}</span>}
              {data.event.published_at && <span>{fmt.date(data.event.published_at)}</span>}
              {!notRelevant && <span>Горизонт: {fmt.horizon(b.horizon)}</span>}
              <span>Значимость для компании: {fmt.pct(b.relevance?.score)}</span>
              <span>Уверенность: {fmt.pct(b.confidence)}</span>
              {data.event.private && <span className="chip">добавлено вами</span>}
              {data.stale && <span className="chip">профиль компании изменился</span>}
            </div>
            <h1>{b.title}</h1>
            <p className="lead">{b.summary}</p>
            {factorNames.length > 0 && (
              <div className="chips" style={{ marginTop: 12 }}>
                {factorNames.map((f) => <span key={f} className="chip">{f}</span>)}
              </div>
            )}
            <KindLegend />
          </div>

          {b.status === "insufficient_data" && (
            <div className="error" style={{ marginBottom: 14 }}>
              Данных недостаточно для уверенного вывода: {b.missing.join("; ")}
            </div>
          )}

          <Section title="Что произошло" q="Факты из первоисточников">
            {s.what_happened.length ? s.what_happened.map(item) : <p className="missing">Подтверждённых фактов нет.</p>}
          </Section>

          <Section title="Почему это важно вашей компании" q="Связь с признаками компании">
            {s.why_it_matters.map(item)}
          </Section>

          {!notRelevant && (
            <Section title="Какие последствия возможны" q="Риски, возможности и факторы">
              {s.consequences.risks.length > 0 && <div className="sub-h risk">Риски</div>}
              {s.consequences.risks.map(item)}
              {s.consequences.opportunities.length > 0 && <div className="sub-h opportunity">Возможности</div>}
              {s.consequences.opportunities.map(item)}
              {s.consequences.watch_factors.length > 0 && <div className="sub-h watch">Что ещё отслеживать</div>}
              {s.consequences.watch_factors.map(item)}
            </Section>
          )}

          {!notRelevant && (
            <Section title="Что можно сделать" q="Направления действий">
              {s.actions.length ? s.actions.map(item) : <p className="missing">Конкретных действий не предложено.</p>}
            </Section>
          )}

          {!notRelevant && (
            <Section title="Как это оценивает банк" q="Центр макроэкономических исследований">
              {s.bank_view.length ? s.bank_view.map(item) : <p className="missing">Аналитика банка по этой теме отсутствует.</p>}
            </Section>
          )}

          <Section title="На чём основан вывод" q="Источники и признаки">
            <div className="srcs">
              {s.evidence.materials.map((id) => (
                <button key={id} className="src" onClick={() => open(id)}>
                  {data.sources[id] ? `${data.sources[id].source}: ${data.sources[id].title}` : id}
                </button>
              ))}
              {s.evidence.alfa.map((id) => (
                <button key={id} className="src" onClick={() => open(id)}>
                  {data.sources[id] ? `${data.sources[id].report}, с. ${data.sources[id].page}` : id}
                </button>
              ))}
              {s.evidence.features.map((f) => <span key={f} className="src feature">Признак: {f}</span>)}
            </div>
            <p className="small muted" style={{ marginTop: 10 }}>
              Проверка: {b.verification.passed ? "все проверки пройдены" : "есть замечания"}
              {b.verification.iterations > 0 && `, доработок: ${b.verification.iterations}`}. Модели: {Object.values(b.models).filter((v, i, a) => a.indexOf(v) === i).map((v) => (v === "rules" ? "правила (mock-режим)" : v)).join(", ") || "—"}.
            </p>
          </Section>

          <div className="panel feedback">
            {voted ? (
              <span>{voted}</span>
            ) : (
              <>
                <span>Вывод полезен?</span>
                <button className="btn small" onClick={() => vote(1, "useful")}>Да</button>
                {REASONS.map(([code, label]) => (
                  <button key={code} className="btn small" onClick={() => vote(-1, code)}>{label}</button>
                ))}
              </>
            )}
            {onRegenerate && (
              <button className="btn small ghost" style={{ marginLeft: "auto" }} onClick={onRegenerate}>
                Пересобрать анализ
              </button>
            )}
          </div>
        </div>

        <div className="rail">
          <div className="panel">
            <div className="tabs" role="tablist">
              <button role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>
                Вопрос по событию
              </button>
              <button role="tab" aria-selected={tab === "trace"} className={tab === "trace" ? "on" : ""} onClick={() => setTab("trace")}>
                Как получен вывод
              </button>
            </div>
            {tab === "chat" ? (
              <Chat insightId={b.insight_id} sources={data.sources} onOpen={open} />
            ) : (
              <TracePanel insightId={b.insight_id} features={s.evidence.features} />
            )}
          </div>
        </div>
      </div>
      {drawer && <SourceDrawer id={drawer.id} quote={drawer.quote} onClose={() => setDrawer(null)} />}
    </>
  );
}

function Section({ title, q, children }: { title: string; q: string; children: React.ReactNode }) {
  return (
    <section className="section">
      <header>
        <h2>{title}</h2>
        <span className="q">{q}</span>
      </header>
      <div className="section-body">{children}</div>
    </section>
  );
}
