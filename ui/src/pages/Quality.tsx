import { useEffect, useState } from "react";
import { api, fmt } from "../api";

export default function Quality() {
  const [m, setM] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState<string | null>(null);
  useEffect(() => {
    api.metrics().then(setM).catch((e) => setError(e.message));
  }, []);
  const refreshNews = async () => {
    try {
      const r = await api.refreshNews();
      setRefresh(
        r.rss_enabled
          ? "Сбор новостей запущен. Обновите страницу через минуту."
          : "Живые источники выключены (RSS_ENABLED=false): будет пересчитан только текущий корпус.",
      );
    } catch (e: any) {
      setRefresh(e.message);
    }
  };
  if (error) return <div className="error">{error}</div>;
  if (!m) return <div className="spinner">Загрузка…</div>;
  const ev = m.offline_eval;
  const b = m.llm.budget;
  return (
    <div>
      <h1>Качество сервиса</h1>
      <p className="muted" style={{ marginTop: 6 }}>Мониторинг контента, персонализации, агента и расходов на модели.</p>

      <div className="panel" style={{ marginTop: 20 }}>
        <div className="kpis">
          <div><div className="small muted">Проверка Verifier пройдена</div><div className="num">{fmt.pct(m.insights.verification_pass_rate)}</div></div>
          <div><div className="small muted">Инсайты с ≥ 2 факторами</div><div className="num">{fmt.pct(m.insights.multi_factor_share)}</div></div>
          <div><div className="small muted">Есть позиция банка</div><div className="num">{fmt.pct(m.insights.bank_view_coverage)}</div></div>
          <div><div className="small muted">Доля полезных оценок</div><div className="num">{m.feedback.up_share == null ? "—" : fmt.pct(m.feedback.up_share)}</div></div>
        </div>
      </div>

      <div className="home-grid" style={{ marginTop: 18 }}>
        <div className="panel panel-pad">
          <h2>Офлайн-оценка на разметке</h2>
          {ev ? (
            <>
              <table className="plain" style={{ marginTop: 10 }}>
                <thead><tr><th>Компания / роль</th><th>precision@5</th><th>nDCG@10</th><th>Точность ленты</th><th>Полнота ленты</th></tr></thead>
                <tbody>
                  {Object.entries(ev.ranking).map(([k, r]: any) => (
                    <tr key={k}><td>{k}</td><td>{r["precision@5"]}</td><td>{r["ndcg@10"]}</td><td>{r.feed_precision}</td><td>{r.feed_recall}</td></tr>
                  ))}
                </tbody>
              </table>
              <p className="small muted" style={{ marginTop: 10 }}>
                Различие лент между компаниями (1 − Jaccard топ-10): {Object.values(ev.personalization_divergence).join(", ")}
              </p>
            </>
          ) : (
            <p className="muted" style={{ marginTop: 10 }}>Запустите make eval, чтобы появились результаты.</p>
          )}
        </div>
        <div className="panel panel-pad">
          <h2>Модели и бюджет</h2>
          <p style={{ marginTop: 10 }}>
            Режим: <b>{b.mode === "live" ? "общий ключ (live)" : "детерминированный (mock)"}</b>. Расход сегодня: {b.spent_rub} из {b.limit_rub} ₽.
          </p>
          <div className="bar" style={{ height: 8, marginTop: 8 }}><i style={{ width: fmt.pct(b.share), background: b.state === "ok" ? "var(--opp)" : "var(--risk)" }} /></div>
          <table className="plain" style={{ marginTop: 14 }}>
            <tbody>
              <tr><td>Запусков агента</td><td>{m.agent.runs}</td></tr>
              <tr><td>Медиана / p95 времени</td><td>{m.agent.median_ms} / {m.agent.p95_ms} мс</td></tr>
              <tr><td>Средняя стоимость запуска</td><td>{m.agent.avg_cost_rub} ₽</td></tr>
              <tr><td>Вызовов моделей за сутки</td><td>{m.llm.calls_24h} (из кэша {m.llm.cache_hits_24h}, ошибок {m.llm.errors_24h})</td></tr>
              <tr><td>Средние итерации доработки</td><td>{m.insights.avg_adapt_iterations}</td></tr>
              <tr><td>Доля «данных недостаточно»</td><td>{fmt.pct(m.insights.insufficient_share)}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="home-grid" style={{ marginTop: 18 }}>
        <div className="panel panel-pad">
          <h2>Продуктовые метрики</h2>
          <table className="plain" style={{ marginTop: 10 }}>
            <tbody>
              <tr><td>Уровни влияния инсайтов</td><td>{Object.entries(m.product.impact_levels).map(([k, v]) => `${k}: ${v}`).join(", ")}</td></tr>
              <tr><td>Обращений после инсайта</td><td>{m.product.requests_total} ({Object.entries(m.product.requests_by_kind).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"})</td></tr>
              <tr><td>Обращений на открытие инсайта</td><td>{m.product.request_rate_per_open ?? "—"}</td></tr>
              <tr><td>Переходов к первоисточнику на открытие</td><td>{m.product.source_open_rate ?? "—"}</td></tr>
              <tr><td>Скрытий на открытие (сигнал нерелевантности)</td><td>{m.product.hide_rate_per_open ?? "—"}</td></tr>
              <tr><td>Компании по статусу в банке</td><td>{Object.entries(m.data_quality.client_status || {}).map(([k, v]) => `${k}: ${v}`).join(", ")}</td></tr>
              <tr><td>Заглушки выручки</td><td>{m.data_quality.revenue_placeholders}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="panel panel-pad">
          <h2>Источники новостей</h2>
          <p className="small muted" style={{ marginTop: 6 }}>
            Живой сбор: {m.news_sources.rss_enabled ? `включён, каждые ${m.news_sources.refresh_min || "—"} мин` : "выключен"}.
            {m.news_sources.last_cycle && ` Последний цикл: ${m.news_sources.last_cycle.finished_at}, новых материалов ${m.news_sources.last_cycle.rss_added ?? 0}.`}
          </p>
          <table className="plain" style={{ marginTop: 10 }}>
            <tbody>
              {Object.entries(m.news_sources.sources).map(([id, s]: any) => (
                <tr key={id}>
                  <td>{s.name}</td>
                  <td>{s.ok ? `работает, новых ${s.added ?? 0}` : `ошибка подряд: ${s.fails_in_row}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button className="btn small" style={{ marginTop: 12 }} onClick={refreshNews}>Обновить новости сейчас</button>
          {refresh && <p className="small" style={{ marginTop: 8 }}>{refresh}</p>}
        </div>
      </div>

      <div className="panel panel-pad" style={{ marginTop: 18 }}>
        <h2>Последние отрицательные оценки</h2>
        {m.feedback.recent_negative.length === 0 ? (
          <p className="muted" style={{ marginTop: 8 }}>Отрицательных оценок пока нет.</p>
        ) : (
          <table className="plain" style={{ marginTop: 10 }}>
            <thead><tr><th>Инсайт</th><th>Причина</th><th>Когда</th></tr></thead>
            <tbody>
              {m.feedback.recent_negative.map((f: any, i: number) => (
                <tr key={i}><td><a href={`#/insight/${f.insight_id}`}>{f.insight_id}</a></td><td>{f.reason}</td><td>{fmt.date(f.ts)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
