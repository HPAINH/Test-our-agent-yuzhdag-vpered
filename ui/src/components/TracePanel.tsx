import { useEffect, useState } from "react";
import { api } from "../api";

const KIND: Record<string, string> = {
  plan: "Планирование", tool: "Инструмент", llm: "Модель", verify: "Самопроверка", retry: "Адаптация",
  finalize: "Итог", error: "Ошибка", ingest: "Обработка",
};

export default function TracePanel({ insightId, features }: { insightId: string; features: string[] }) {
  const [t, setT] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.trace(insightId).then(setT).catch((e) => setError(e.message));
  }, [insightId]);
  if (error) return <div className="trace error">{error}</div>;
  if (!t) return <div className="spinner">Загрузка трассы…</div>;
  return (
    <div className="trace">
      <div className="meta-row" style={{ padding: "8px 0" }}>
        <span>{t.steps.length} шагов</span>
        <span>{t.tokens.toLocaleString("ru-RU")} токенов</span>
        <span>{t.cost_rub.toFixed(2)} ₽</span>
        {t.duration_ms != null && <span>{(t.duration_ms / 1000).toFixed(1)} с</span>}
      </div>
      {features.length > 0 && (
        <p className="small muted" style={{ marginBottom: 6 }}>
          Признаки компании, повлиявшие на вывод: {features.join(", ")}
        </p>
      )}
      {t.steps.map((s: any) => (
        <div key={s.n} className={`step ${s.kind}`}>
          <div className="step-n">{s.n}</div>
          <div>
            <div className="step-name">
              {KIND[s.kind] || s.kind}: {s.name}
              {s.model && <span className="small muted"> · {s.model}</span>}
            </div>
            {s.output_summary && <div className="step-out">{s.output_summary}</div>}
            {s.kind === "verify" && s.detail?.checks && (
              <table className="checks">
                <tbody>
                  {s.detail.checks.map((c: any) => (
                    <tr key={c.n}>
                      <td>{c.pass ? "пройдено" : "не пройдено"}</td>
                      <td>{c.name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {(s.input_summary || s.detail?.result_preview) && (
              <details>
                <summary>Подробнее</summary>
                {s.input_summary && <pre>Вход: {s.input_summary}</pre>}
                {s.detail?.result_preview && <pre>{s.detail.result_preview}</pre>}
              </details>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
