import { useEffect, useState } from "react";
import { api, fmt } from "../api";

export default function SourceDrawer({ id, quote, onClose }: { id: string; quote?: string | null; onClose: () => void }) {
  const [m, setM] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.material(id).then(setM).catch((e) => setError(e.message));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, onClose]);

  const highlight = (text: string) => {
    const q = (quote || "").replace(/…$/, "").trim();
    const i = q ? text.indexOf(q) : -1;
    if (i < 0) return text;
    return (
      <>
        {text.slice(0, i)}
        <mark>{text.slice(i, i + q.length)}</mark>
        {text.slice(i + q.length)}
      </>
    );
  };

  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Первоисточник">
        <header>
          <div>
            <div className="small muted">{m ? (m.kind === "alfa" ? "Аналитика Альфа-Банка" : "Новость") : "Первоисточник"}</div>
            <h2 style={{ marginTop: 4 }}>{m?.title || "Загрузка…"}</h2>
            {m && (
              <div className="meta-row" style={{ marginTop: 6 }}>
                <span>{m.source}</span>
                {m.published_at && <span>{fmt.date(m.published_at)}</span>}
                {m.page && <span>страница {m.page}</span>}
                {m.synthetic && <span className="chip">синтетические данные</span>}
              </div>
            )}
          </div>
          <button className="btn ghost small" onClick={onClose} aria-label="Закрыть">
            Закрыть
          </button>
        </header>
        <div className="body">
          {error && <div className="error">{error}</div>}
          {m && highlight(m.text)}
          {m?.url && !m.synthetic && (
            <p style={{ marginTop: 16 }}>
              <a href={m.url} target="_blank" rel="noreferrer">
                Открыть оригинал
              </a>
            </p>
          )}
        </div>
      </aside>
    </>
  );
}
