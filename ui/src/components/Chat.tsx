import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Claim, SourceMeta } from "../api";
import { SourceChip } from "./Claims";

interface Msg {
  role: "user" | "bot";
  text: string;
  claims?: Claim[];
}

const QUICK = ["Только риски", "Влияние на долг", "Что говорит банк", "Покажи первоисточник", "Сравни с прошлыми событиями", "Что делать?"];

export default function Chat({
  insightId, sources, onOpen,
}: {
  insightId?: string;
  sources: Record<string, SourceMeta>;
  onOpen: (id: string, quote?: string | null) => void;
}) {
  const [log, setLog] = useState<Msg[]>([
    { role: "bot", text: "Задайте вопрос об этом событии: я отвечаю по первоисточникам, аналитике банка и профилю вашей компании." },
  ]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ block: "nearest" }), [log]);

  const ask = async (q: string) => {
    if (!q.trim() || busy) return;
    setLog((l) => [...l, { role: "user", text: q }]);
    setText("");
    setBusy(true);
    try {
      const history = log.slice(-10).map((m) => ({ role: m.role, text: m.text }));
      const r = await api.chat(q, insightId, history);
      const text = typeof r?.text === "string" && r.text ? r.text : "Сервер вернул пустой ответ. Попробуйте ещё раз.";
      const claims = Array.isArray(r?.claims) ? r.claims.filter((c) => c && Array.isArray(c.sources)) : [];
      setLog((l) => [...l, { role: "bot", text, claims }]);
    } catch (e: any) {
      setLog((l) => [...l, { role: "bot", text: `Не удалось получить ответ: ${e?.message || "ошибка сети"}` }]);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    ask(text);
  };

  return (
    <div className="chat">
      <div className="chat-log" aria-live="polite">
        {log.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.text}
            {m.claims && m.claims.some((c) => (c.sources || []).some((s) => s.type === "material" || s.type === "alfa")) && (
              <div className="srcs">
                {m.claims.flatMap((c) =>
                  (c.sources || [])
                    .filter((s) => s.type === "material" || s.type === "alfa")
                    .map((s, j) => <SourceChip key={c.id + j} s={s} sources={sources} onOpen={onOpen} />),
                )}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="msg bot muted">Ищу ответ…</div>}
        <div ref={end} />
      </div>
      <div className="quick">
        {QUICK.map((q) => (
          <button key={q} className="btn small" onClick={() => { api.uiEvent("chat_quick", q); ask(q); }} disabled={busy}>
            {q}
          </button>
        ))}
      </div>
      <p className="disclaimer" style={{ margin: "8px 16px 0" }}>
        Ответы готовит модель по первоисточникам и аналитике банка; это не инвестиционная рекомендация.
      </p>
      <form className="chat-form" onSubmit={submit}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Например: как это скажется на марже?" aria-label="Вопрос" />
        <button className="btn primary" disabled={busy || !text.trim()}>
          Спросить
        </button>
      </form>
    </div>
  );
}
