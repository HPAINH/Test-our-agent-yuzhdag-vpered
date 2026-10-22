import { useEffect, useState } from "react";
import { api, FeedCard, fmt } from "../api";
import { go } from "../router";

const POL = { risk: "Риск", opportunity: "Возможность", mixed: "Требует внимания" } as const;
const IMPACT = { high: "высокое", medium: "среднее", low: "низкое" } as const;
const KINDS: [string, string][] = [["", "Все"], ["risk", "Риски"], ["opportunity", "Возможности"], ["mixed", "Прочее"]];
const HORIZONS: [string, string][] = [["", "Любой горизонт"], ["0-1m", "до 1 месяца"], ["0-3m", "до 3 месяцев"], ["1-3m", "1–3 месяца"], ["3-12m", "3–12 месяцев"]];

export default function Feed() {
  const [items, setItems] = useState<FeedCard[] | null>(null);
  const [total, setTotal] = useState(0);
  const [digest, setDigest] = useState<string | null>(null);
  const [factors, setFactors] = useState<Record<string, string>>({});
  const [kind, setKind] = useState("");
  const [factor, setFactor] = useState("");
  const [horizon, setHorizon] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.digest().then((d) => setDigest(d.text)).catch(() => undefined);
    api.meta().then((m) => setFactors(m.factors)).catch(() => undefined);
  }, []);

  useEffect(() => {
    const params: Record<string, string> = {};
    if (kind) params.kind = kind;
    if (factor) params.factor = factor;
    if (horizon) params.horizon = horizon;
    if (showHidden) params.hidden = "only";
    setItems(null);
    api.feed(params).then((r) => { setItems(r.items); setTotal(r.total); }).catch((e) => setError(e.message));
    if (kind || factor || horizon) api.uiEvent("filter", `${kind}|${factor}|${horizon}`);
  }, [kind, factor, horizon, showHidden, reload]);

  const open = (c: FeedCard) => go(c.insight_id ? `/insight/${c.insight_id}` : `/event/${c.event_id}`);
  const toggleHidden = async (c: FeedCard) => {
    try {
      if (c.hidden) await api.unhideEvent(c.event_id);
      else await api.hideEvent(c.event_id);
      setReload((n) => n + 1);
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div>
      <div className="feed-head">
        <div>
          <h1>Пульс бизнеса</h1>
          <p className="muted" style={{ marginTop: 6 }}>События, отобранные под профиль вашей компании и вашу роль</p>
        </div>
        <button className="btn" onClick={() => go("/analyze")}>Проверить свою новость</button>
      </div>
      {digest && (
        <div className="digest">
          <h3 style={{ marginBottom: 6 }}>Сводка дня</h3>
          <p>{digest}</p>
        </div>
      )}
      <div className="filters">
        <div className="seg" role="group" aria-label="Тип события">
          {KINDS.map(([v, l]) => (
            <button key={v} className={kind === v ? "on" : ""} onClick={() => setKind(v)} aria-pressed={kind === v}>
              {l}
            </button>
          ))}
        </div>
        <select className="input" value={factor} onChange={(e) => setFactor(e.target.value)} aria-label="Фактор">
          <option value="">Все факторы</option>
          {Object.entries(factors).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className="input" value={horizon} onChange={(e) => setHorizon(e.target.value)} aria-label="Горизонт">
          {HORIZONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <label className="small">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> Скрытые
        </label>
        <span className="small muted">Найдено: {total}</span>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="feed-list">
        {items === null && <div className="spinner">Загрузка…</div>}
        {items?.length === 0 && (
          <div className="empty">
            По выбранным фильтрам событий нет. Сбросьте фильтры или проверьте свою новость.
          </div>
        )}
        {items?.map((c) => (
          <div key={c.event_id} className={`feed-item ${c.hidden ? "is-hidden" : ""}`}>
            <button className="feed-row" onClick={() => open(c)}>
              <div>
                <div className="meta-row">
                  <span className={`pol ${c.polarity}`}>{POL[c.polarity]}</span>
                  <span>{fmt.date(c.published_at)}</span>
                  {c.impact && <span>Влияние: {IMPACT[c.impact.level]}</span>}
                  {c.horizon && <span>{fmt.horizon(c.horizon)}</span>}
                  {c.has_bank_view && <span>есть позиция банка</span>}
                  {c.private && <span className="chip">добавлено вами</span>}
                </div>
                <h3>{c.title}</h3>
                <p className="sum">{c.summary}</p>
                <div className="chips" style={{ marginTop: 8 }}>
                  {c.factors.map((f) => <span key={f.code} className="chip">{f.name}</span>)}
                  <span className="small muted">Источники: {c.sources.map((s) => s.source.replace("Синтетическая лента: ", "")).join(", ")}</span>
                </div>
                {!c.generated && <div className="pending-note" style={{ marginTop: 6 }}>Анализ подготовится при открытии</div>}
              </div>
              <div className="score">
                <div className="small muted">Значимость</div>
                <div className="val">{fmt.pct(c.score)}</div>
                <div className="bar"><i style={{ width: fmt.pct(c.score) }} /></div>
              </div>
            </button>
            <button className="btn small ghost hide-btn" onClick={() => toggleHidden(c)}>
              {c.hidden ? "Вернуть" : "Скрыть"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
