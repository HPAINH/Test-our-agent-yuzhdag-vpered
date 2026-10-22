import { useEffect, useRef, useState } from "react";
import { api, FeedCard, fmt, setToken } from "../api";

const POL = { risk: "Риск", opportunity: "Возможность", mixed: "Требует внимания" } as const;

type Widget = { top: FeedCard[]; total: number; new_count: number; risk_count: number };

const framed = window.parent !== window;

function post(msg: Record<string, unknown>) {
  if (framed) window.parent.postMessage({ source: "pulse", ...msg }, "*");
}

export default function Embed({ token }: { token: string }) {
  const [w, setW] = useState<Widget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    window.history.replaceState(null, "", `${window.location.pathname}#/embed`);
    if (!token) {
      setError("Нет ссылки входа от интернет-банка");
      return;
    }
    api
      .launch(token)
      .then((r) => {
        setToken(r.token, false);
        return api.widget();
      })
      .then((data) => {
        setW(data);
        post({ type: "pulse:badge", count: data.new_count, risks: data.risk_count });
      })
      .catch((e) => setError(e.message));
  }, [token]);

  useEffect(() => {
    if (!root.current) return;
    const ro = new ResizeObserver(() => post({ type: "pulse:resize", height: root.current?.offsetHeight || 0 }));
    ro.observe(root.current);
    return () => ro.disconnect();
  }, []);

  const open = (target: string) => post({ type: "pulse:open", target });

  return (
    <div className="embed" ref={root}>
      <section className="pulse" aria-labelledby="embed-title">
        <div className="pulse-head">
          <div>
            <h2 id="embed-title">Пульс бизнеса</h2>
            <p className="pulse-count">
              {w ? `${w.total} значимых событий, ${w.new_count} за последние 3 дня` : error ? "" : "Загрузка…"}
            </p>
          </div>
          {w && w.risk_count > 0 && <span className="pol risk">{w.risk_count} риска в фокусе</span>}
        </div>
        {error && <div className="error" style={{ margin: "0 20px 16px" }}>{error}</div>}
        {w?.top.map((c) => (
          <button
            key={c.event_id}
            className="pulse-item"
            onClick={() => open(c.insight_id ? `insight/${c.insight_id}` : `event/${c.event_id}`)}
          >
            <div className="meta-row">
              <span className={`pol ${c.polarity}`}>{POL[c.polarity]}</span>
              <span>{fmt.date(c.published_at)}</span>
            </div>
            <h3>{c.title}</h3>
          </button>
        ))}
        {w && w.top.length === 0 && <div className="empty">Значимых для компании событий пока нет.</div>}
        {w && (
          <div className="pulse-foot">
            <button className="btn primary" onClick={() => open("feed")}>
              Открыть все события
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
