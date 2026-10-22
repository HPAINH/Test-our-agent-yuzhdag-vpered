import { useMemo, useState } from "react";
import { api, Claim, fmt, InsightPayload } from "../api";
import { ClaimItem, KindLegend } from "./Claims";
import Chat from "./Chat";
import SourceDrawer from "./SourceDrawer";
import TracePanel from "./TracePanel";
import ErrorBoundary from "./ErrorBoundary";

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
  const [notice, setNotice] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
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
  const request = async (kind: "manager" | "callback" | "application", product?: string | null) => {
    try {
      const r = await api.createRequest({ kind, insight_id: b.insight_id, product });
      setNotice(`${r.message} Номер обращения: ${r.id}. В прототипе заявка сохраняется в сервисе, CRM банка не вызывается.`);
    } catch (e: any) {
      setNotice(`Не удалось отправить: ${e.message}`);
    }
  };
  const toggleHidden = async () => {
    try {
      if (hidden) await api.unhideEvent(b.event_id);
      else await api.hideEvent(b.event_id);
      setHidden(!hidden);
    } catch (e: any) {
      setNotice(`Не удалось изменить: ${e.message}`);
    }
  };
  const item = (c: Claim) => (
    <ClaimItem key={c.id} c={c} sources={data.sources} claims={claims} onOpen={open}>
      {c.bank_product_title && (
        <div className="offer">
          <span>
            <b>Продукт банка, если уместен:</b> {c.bank_product_title}
          </span>
          <button className="btn small" onClick={() => request("application", c.bank_product)}>
            Оставить заявку
          </button>
        </div>
      )}
    </ClaimItem>
  );
  const scale = data.impact_scale || {};
  const impact = b.impact;

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
            {impact && !notRelevant && (
              <div className="impact" title={scale[impact.level]?.meaning}>
                <div>
                  <div className="small muted">Влияние на компанию</div>
                  <div className={`impact-level ${impact.level}`}>
                    {scale[impact.level]?.label || impact.level}, {impact.score} из 100
                  </div>
                </div>
                <div>
                  <div className="small">{scale[impact.level]?.meaning}</div>
                  <ul>{impact.basis.map((x) => <li key={x}>{x}</li>)}</ul>
                  {impact.estimate && <div className="estimate">{impact.estimate}</div>}
                </div>
              </div>
            )}
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
              <div className="cta-row">
                <button className="btn small" onClick={() => request("manager")}>Обсудить с менеджером</button>
                <button className="btn small" onClick={() => request("callback")}>Заказать обратный звонок</button>
              </div>
              {notice && <div className="notice" role="status">{notice}</div>}
            </Section>
          )}

          {!notRelevant && (
            <Section title="Как это оценивает банк" q="Центр макроэкономических исследований">
              {s.bank_view.length ? (
                <>
                  <p className="section-note">
                    Здесь только цитаты из аналитики Альфа-Банка. Выводы и гипотезы выше подготовлены моделью и не являются
                    позицией банка.
                  </p>
                  {s.bank_view.map(item)}
                </>
              ) : (
                <p className="missing">
                  Аналитики банка по этой теме нет. Все выводы и гипотезы выше подготовлены моделью и не являются позицией банка.
                </p>
              )}
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
            <button className="btn small ghost" style={{ marginLeft: "auto" }} onClick={toggleHidden}>
              {hidden ? "Вернуть в ленту" : "Скрыть из ленты"}
            </button>
            {onRegenerate && (
              <button className="btn small ghost" onClick={onRegenerate}>
                Пересобрать анализ
              </button>
            )}
          </div>
          <p className="disclaimer">{data.disclaimer}</p>
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
            <ErrorBoundary resetKey={tab}>
              {tab === "chat" ? (
                <Chat insightId={b.insight_id} sources={data.sources} onOpen={open} />
              ) : (
                <TracePanel insightId={b.insight_id} features={s.evidence.features} />
              )}
            </ErrorBoundary>
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
