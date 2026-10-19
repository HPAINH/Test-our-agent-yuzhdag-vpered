import { useEffect, useState } from "react";
import { api, FeedCard, fmt, User } from "../api";
import { go } from "../router";

const POL = { risk: "Риск", opportunity: "Возможность", mixed: "Требует внимания" } as const;

export default function Home({ user }: { user: User }) {
  const [w, setW] = useState<{ top: FeedCard[]; total: number; new_count: number; risk_count: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.widget().then(setW).catch((e) => setError(e.message));
  }, []);

  const openCard = (c: FeedCard) => go(c.insight_id ? `/insight/${c.insight_id}` : `/event/${c.event_id}`);

  return (
    <div className="home-grid">
      <div>
        <h1>Добрый день, {user.display_name.split(" ")[0]}</h1>
        <p className="muted" style={{ marginTop: 6 }}>
          {user.role_name}. В фокусе: {user.role_focus?.toLowerCase()}.
        </p>
        <div className="panel" style={{ marginTop: 22 }}>
          <div className="accounts">
            <div>
              <div className="small muted">Расчётный счёт</div>
              <div className="num">1 284 503 120 ₽</div>
            </div>
            <div>
              <div className="small muted">Валютный счёт, CNY</div>
              <div className="num">48 210 900 ¥</div>
            </div>
            <div>
              <div className="small muted">Платежи на подписи</div>
              <div className="num">7</div>
            </div>
          </div>
        </div>
        <p className="demo-note" style={{ marginTop: 10 }}>
          Остатки и платежи — заглушки интерфейса банка. В прототипе работает только раздел «Пульс бизнеса».
        </p>
        <div className="quick-actions" style={{ marginTop: 22 }}>
          <button className="btn" disabled>Новый платёж</button>
          <button className="btn" disabled>Выписка</button>
          <button className="btn" onClick={() => go("/analyze")}>Проверить новость</button>
          <button className="btn" onClick={() => go("/profile")}>Профиль компании</button>
        </div>
      </div>

      <aside className="pulse" aria-labelledby="pulse-title">
        <div className="pulse-head">
          <div>
            <h2 id="pulse-title">Пульс бизнеса</h2>
            <p className="pulse-count">
              {w ? `${w.total} значимых событий, ${w.new_count} за последние 3 дня` : "Загрузка…"}
            </p>
          </div>
          {w && w.risk_count > 0 && <span className="pol risk">{w.risk_count} риска в фокусе</span>}
        </div>
        {error && <div className="error" style={{ margin: "0 20px 16px" }}>{error}</div>}
        {w?.top.map((c) => (
          <button key={c.event_id} className="pulse-item" onClick={() => openCard(c)}>
            <div className="meta-row">
              <span className={`pol ${c.polarity}`}>{POL[c.polarity]}</span>
              <span>{fmt.date(c.published_at)}</span>
            </div>
            <h3>{c.title}</h3>
          </button>
        ))}
        {w && w.top.length === 0 && <div className="empty">Значимых для компании событий пока нет.</div>}
        <div className="pulse-foot">
          <button className="btn primary" onClick={() => go("/feed")}>
            Открыть все события
          </button>
        </div>
      </aside>
    </div>
  );
}
