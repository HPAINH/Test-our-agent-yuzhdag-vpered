import { useEffect, useState } from "react";
import { api, fmt } from "../api";

const POL = { risk: "риск", opportunity: "возможность", mixed: "двоякое влияние" } as Record<string, string>;
const PRODUCTS: Record<string, string> = {
  rko: "РКО", guarantee: "Банковские гарантии", loan: "Кредиты", leasing: "Лизинг", deposit: "Депозиты",
  payroll: "Зарплатный проект", factoring: "Факторинг",
};

const STATUS: Record<string, string> = {
  client: "действующий клиент",
  prospect: "потенциальный клиент",
  closed: "бывший клиент",
  unknown: "нет данных",
};

export default function Profile() {
  const [d, setD] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [extra, setExtra] = useState("");
  const load = () => api.profile().then(setD).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    api.meta().then((m) => setNames(m.factors)).catch(() => undefined);
  }, []);
  const hint = async (factor: string, mode: "boost" | "mute" | "reset") => {
    setBusy(factor);
    try {
      await api.factorHint(factor, mode);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };
  if (error) return <div className="error">{error}</div>;
  if (!d) return <div className="spinner">Загрузка…</div>;
  const p = d.profile;
  const hints: Record<string, string> = p.user_hints || {};
  const muted = Object.entries(hints).filter(([, m]) => m === "mute").map(([f]) => f);
  const present = new Set(p.factors.map((f: any) => f.factor));
  const addable = Object.keys(names).filter((f) => !present.has(f) && !muted.includes(f));
  const facts: [string, string][] = [
    ["Отрасль", `${p.industry.name} (ОКВЭД ${p.industry.okved})`],
    [
      "Выручка за 2025 год",
      p.data_quality?.revenue_placeholder ? "нет данных (в источнике техническая заглушка)" : fmt.money(p.scale.revenue_rub),
    ],
    [
      "Сегмент",
      `${p.is_large ? "крупный бизнес" : "средний бизнес"} — по выручке ${p.scale.large_basis === "group" ? "группы" : "компании"}: ${fmt.money(p.scale.large_basis_revenue_rub)}`,
    ],
    ["Место в отрасли по данным банка", p.scale.rank_in_okved ? `${p.scale.rank_in_okved} из ${p.scale.peers_in_okved}` : "н/д"],
    ["Численность", p.scale.workers || "н/д"],
    ["Регион и город обслуживания", `${p.geo.region}, ${p.geo.city}`],
    ["Статус в банке", STATUS[p.org.client_status] || p.org.client_type || "н/д"],
    ["Стадия бизнеса", p.org.stage || "н/д"],
    ["ВЭД", p.foreign_trade.flag ? `да, ${p.foreign_trade.country || "страна не указана"}` : "нет"],
    ["Группа компаний", p.group?.member_count > 1 ? `${p.group.member_count} компании` : "нет"],
  ];
  return (
    <div>
      <h1>Профиль компании</h1>
      <p className="muted" style={{ marginTop: 6, maxWidth: 760 }}>
        По этим признакам сервис решает, какие события важны компании. Чувствительность к факторам рассчитана правилами
        из признаков и связей; у каждого веса видны основания.
      </p>
      <div className="panel" style={{ marginTop: 20 }}>
        <div className="facts-grid">
          {facts.map(([k, v]) => (
            <div key={k}>
              <div className="small muted">{k}</div>
              <div>{v}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel panel-pad" style={{ marginTop: 18 }}>
        <h2>Чувствительность к факторам</h2>
        <p className="small muted" style={{ marginTop: 4 }}>
          Уточните профиль: отметьте, что для компании важнее или что не учитывать. Отметки сохраняются как «указано
          пользователем», меняют ранжирование ленты и видны в основаниях выводов.
        </p>
        <div style={{ marginTop: 10 }}>
          {p.factors.map((f: any) => (
            <div key={f.factor} className="factor-row with-actions">
              <div>
                <div>{f.name}</div>
                <div className="small muted">{POL[f.polarity]}</div>
              </div>
              <div>
                <div className={`fbar ${f.polarity}`}><i style={{ width: `${Math.round(f.weight * 100)}%` }} /></div>
                <div className="small muted" style={{ marginTop: 4 }}>
                  {f.drivers
                    .filter((x: string) => x !== "base")
                    .map((x: string) => (x.startsWith("USER_INPUT") ? "указано пользователем" : x))
                    .join(", ") || "базовая чувствительность"}
                </div>
              </div>
              <div style={{ textAlign: "right", fontWeight: 600 }}>{f.weight.toFixed(2)}</div>
              <div className="factor-actions">
                <button
                  className={`btn small ${hints[f.factor] === "boost" ? "on" : ""}`}
                  disabled={busy === f.factor}
                  onClick={() => hint(f.factor, hints[f.factor] === "boost" ? "reset" : "boost")}
                >
                  Важно
                </button>
                <button className="btn small" disabled={busy === f.factor} onClick={() => hint(f.factor, "mute")}>
                  Не учитывать
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="filters" style={{ marginTop: 12 }}>
          <select className="input" value={extra} onChange={(e) => setExtra(e.target.value)} aria-label="Добавить фактор">
            <option value="">Добавить важный фактор…</option>
            {addable.map((f) => <option key={f} value={f}>{names[f]}</option>)}
          </select>
          <button className="btn small" disabled={!extra} onClick={() => { hint(extra, "boost"); setExtra(""); }}>
            Добавить
          </button>
          {muted.length > 0 && (
            <span className="small muted">
              Не учитываются:{" "}
              {muted.map((f) => (
                <button key={f} className="btn small ghost" onClick={() => hint(f, "reset")}>
                  {names[f] || f} ✕
                </button>
              ))}
            </span>
          )}
        </div>
      </div>

      <div className="home-grid" style={{ marginTop: 18 }}>
        <div className="panel panel-pad">
          <h2>Ключевые контрагенты</h2>
          <table className="plain" style={{ marginTop: 10 }}>
            <thead>
              <tr><th>Контрагент</th><th>Платежи</th><th>Отрасль</th><th style={{ textAlign: "right" }}>Доля</th></tr>
            </thead>
            <tbody>
              {p.counterparties.map((c: any) => (
                <tr key={c.company_id + c.direction}>
                  <td>{c.company_id}{c.same_group ? " (группа)" : ""}</td>
                  <td>{c.direction === "in" ? "входящие" : "исходящие"}</td>
                  <td>{c.known === false ? "нет в выборке банка" : c.okved_name || "н/д"}</td>
                  <td style={{ textAlign: "right" }}>{fmt.pct(c.share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 10 }}>
            Платежи показывают финансовое взаимодействие, но не его характер. Переводы между собственными счетами
            компании исключены.
          </p>
          {p.concentration?.length > 0 && (
            <p className="small" style={{ marginTop: 10, color: "var(--risk)" }}>
              Концентрация: на одного контрагента приходится больше 30% платежей.
            </p>
          )}
        </div>
        <div className="panel panel-pad">
          <h2>Продукты банка</h2>
          <div className="chips" style={{ marginTop: 10 }}>
            {Object.entries(p.products).map(([k, v]) => (
              <span key={k} className="chip" style={{ opacity: v ? 1 : 0.45 }}>
                {PRODUCTS[k] || k}: {v ? "есть" : "нет"}
              </span>
            ))}
          </div>
          {d.group_members.length > 1 && (
            <>
              <h3 style={{ marginTop: 18 }}>Группа {p.group.group_id}</h3>
              <ul className="small" style={{ paddingLeft: 18 }}>
                {d.group_members.map((g: any) => (
                  <li key={g.id}>{g.id}: {g.industry}, {fmt.money(g.revenue_rub)}</li>
                ))}
              </ul>
            </>
          )}
          {p.implicit_links?.length > 0 && (
            <p className="small muted" style={{ marginTop: 10 }}>
              Связи через общих физлиц вне группы: {p.implicit_links.map((l: any) => l.company_id).join(", ")}
            </p>
          )}
          {p.data_quality?.missing?.length > 0 && (
            <p className="small muted" style={{ marginTop: 10 }}>Нет данных: {p.data_quality.missing.join(", ")}</p>
          )}
        </div>
      </div>
    </div>
  );
}
