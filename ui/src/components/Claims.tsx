import { Claim, SourceMeta, SourceRef } from "../api";

export const KIND_LABEL: Record<string, string> = {
  fact: "Факт",
  bank: "Альфа-Банк",
  ai: "Вывод модели",
  hypothesis: "Гипотеза",
};

export function KindLegend() {
  return (
    <div className="legend" aria-label="Обозначения">
      <span><b className="kind fact">Факт</b> подтверждено первоисточником</span>
      <span><b className="kind bank">Альфа-Банк</b> цитата аналитиков банка</span>
      <span><b className="kind ai">Вывод модели</b> связь с вашей компанией; не позиция банка</span>
      <span><b className="kind hypothesis">Гипотеза</b> возможный сценарий с основаниями, не прогноз</span>
    </div>
  );
}

export function SourceChip({
  s, sources, claims, onOpen,
}: {
  s: SourceRef;
  sources: Record<string, SourceMeta>;
  claims?: Record<string, Claim>;
  onOpen: (id: string, quote?: string | null) => void;
}) {
  if (s.type === "feature") {
    return (
      <span className="src feature" title={s.label || s.id}>
        Признак: {s.label || s.id}
      </span>
    );
  }
  if (s.type === "claim") {
    const c = claims?.[s.id];
    return (
      <span className="src feature" title={c?.text}>
        Из вывода: {c ? c.text.slice(0, 48) + (c.text.length > 48 ? "…" : "") : s.id}
      </span>
    );
  }
  if (s.type === "event") return null;
  const meta = sources[s.id];
  const label =
    meta?.kind === "alfa"
      ? `${meta.report}, с. ${meta.page}`
      : meta
        ? `${meta.source}: ${meta.title}`
        : s.label || s.id;
  return (
    <button className="src" onClick={() => onOpen(s.id, s.quote)} title="Открыть первоисточник">
      {label}
    </button>
  );
}

export function ClaimItem({
  c, sources, claims, onOpen, children,
}: {
  c: Claim;
  sources: Record<string, SourceMeta>;
  claims?: Record<string, Claim>;
  onOpen: (id: string, quote?: string | null) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className={`claim ${c.kind}`}>
      <div className={`kind ${c.kind}`}>{KIND_LABEL[c.kind]}</div>
      <div>
        <p className="claim-text">{c.text}</p>
        {c.sources.length > 0 && (
          <div className="srcs">
            {c.sources.map((s, i) => (
              <SourceChip key={i} s={s} sources={sources} claims={claims} onOpen={onOpen} />
            ))}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
