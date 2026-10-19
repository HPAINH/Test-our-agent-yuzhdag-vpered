import { useEffect, useState } from "react";
import { api, InsightPayload } from "../api";
import InsightView from "../components/InsightView";
import { go } from "../router";

export default function InsightPage({ insightId, eventId }: { insightId?: string; eventId?: string }) {
  const [data, setData] = useState<InsightPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const p = insightId ? api.insight(insightId) : api.eventInsight(eventId!);
    p.then((d) => {
      setData(d);
      if (eventId) window.history.replaceState(null, "", `#/insight/${d.insight.insight_id}`);
    }).catch((e) => setError(e.message));
  }, [insightId, eventId]);

  const regenerate = async () => {
    if (!data) return;
    setBusy(true);
    try {
      const d = await api.regenerate(data.insight.insight_id);
      setData(d);
      window.history.replaceState(null, "", `#/insight/${d.insight.insight_id}`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <button className="btn ghost small back" onClick={() => go("/feed")}>
        К ленте событий
      </button>
      {error && <div className="error">{error}</div>}
      {!data && !error && <div className="spinner">{eventId ? "Агент готовит анализ события…" : "Загрузка…"}</div>}
      {busy && <div className="spinner">Агент пересобирает анализ…</div>}
      {data && !busy && <InsightView data={data} onRegenerate={regenerate} />}
    </div>
  );
}
