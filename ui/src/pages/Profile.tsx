import { useEffect, useState } from "react";
import { api, fmt } from "../api";

const POL = { risk: "риск", opportunity: "возможность", mixed: "двоякое влияние" } as Record<string, string>;
const PRODUCTS: Record<string, string> = {
  rko: "РКО", guarantee: "Банковские гарантии", loan: "Кредиты", leasing: "Лизинг", deposit: "Депозиты",
  payroll: "Зарплатный проект", factoring: "Факторинг",
};

export default function Profile() {
  const [d, setD] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.profile().then(setD).catch((e) => setError(e.message));
  }, []);
  if (error) return <div className="error">{error}</div>;
  if (!d) return <div className="spinner">Загрузка…</div>;
  const p = d.profile;
  const facts: [string, string][] = [
    ["Отрасль", `${p.industry.name} (ОКВЭД ${p.industry.okved})`],
    ["Выручка", fmt.money(p.scale.revenue_rub)],
    ["Место в отрасли по данным банка", p.scale.rank_in_okved ? `${p.scale.rank_in_okved} из ${p.scale.peers_in_okved}` : "н/д"],
    ["Численность", p.scale.workers || "н/д"],
    ["Регион", `${p.geo.region}, ${p.geo.city}`],
    ["Тип клиента", p.org.client_type || "н/д"],
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
        <div style={{ marginTop: 10 }}>
          {p.factors.map((f: any) => (
            <div key={f.factor} className="factor-row">
              <div>
                <div>{f.name}</div>
                <div className="small muted">{POL[f.polarity]}</div>
              </div>
              <div>
                <div className={`fbar ${f.polarity}`}><i style={{ width: `${Math.round(f.weight * 100)}%` }} /></div>
                <div className="small muted" style={{ marginTop: 4 }}>
                  {f.drivers.filter((x: string) => x !== "base").join(", ") || "базовая чувствительность"}
                </div>
              </div>
              <div style={{ textAlign: "right", fontWeight: 600 }}>{f.weight.toFixed(2)}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="home-grid" style={{ marginTop: 18 }}>
        <div className="panel panel-pad">
          <h2>Ключевые контрагенты</h2>
          <table className="plain" style={{ marginTop: 10 }}>
            <thead>
              <tr><th>Контрагент</th><th>Роль</th><th>Отрасль</th><th style={{ textAlign: "right" }}>Доля платежей</th></tr>
            </thead>
            <tbody>
              {p.counterparties.map((c: any) => (
                <tr key={c.company_id + c.direction}>
                  <td>{c.company_id}{c.same_group ? " (группа)" : ""}</td>
                  <td>{c.direction === "in" ? "покупатель" : "поставщик"}</td>
                  <td>{c.okved_name || "н/д"}</td>
                  <td style={{ textAlign: "right" }}>{fmt.pct(c.share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
