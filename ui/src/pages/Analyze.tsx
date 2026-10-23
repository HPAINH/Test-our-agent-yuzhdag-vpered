import { FormEvent, useState } from "react";
import { analyzeStream, InsightPayload, User } from "../api";
import InsightView from "../components/InsightView";

const SAMPLE = {
  title: "Крупные банки снизили ставки по рыночной ипотеке",
  text:
    "Крупнейшие банки снизили ставки по рыночной ипотеке на 1,5–2 процентных пункта вслед за снижением доходностей ОФЗ. " +
    "По оценке участников рынка, это может оживить спрос на жильё в новостройках в ноябре–декабре. " +
    "Застройщики Екатеринбурга и Москвы ожидают роста продаж квартир на 10–15%.",
};

const STEP_LABEL: Record<string, string> = {
  ingest: "Обработка", plan: "Планирование", tool: "Инструмент", llm: "Модель", verify: "Самопроверка",
  retry: "Адаптация", finalize: "Итог", error: "Ошибка",
};

export default function Analyze({ user }: { user: User }) {
  const [mode, setMode] = useState<"text" | "url" | "file">("text");
  const [file, setFile] = useState<{ name: string; b64: string } | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [steps, setSteps] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InsightPayload | null>(null);

  const run = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    setSteps([]);
    try {
      const body =
        mode === "url"
          ? { url }
          : mode === "file" && file
            ? { file_name: file.name, file_b64: file.b64, title: title || undefined }
            : { text, title: title || undefined };
      const r = await analyzeStream(body, (s) => setSteps((x) => [...x, s]));
      setResult(r);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h1>Проверить новость</h1>
      <p className="muted" style={{ marginTop: 6, maxWidth: 720 }}>
        Вставьте текст или ссылку на новость, которой нет в ленте. Агент оценит её значимость для компании
        с ролью «{user.role_name.toLowerCase()}» и подготовит интерпретацию со ссылками на источники.
      </p>
      <div className="analyze-grid" style={{ marginTop: 20 }}>
        <form className="panel panel-pad" onSubmit={run}>
          <div className="seg" role="group" aria-label="Способ ввода" style={{ marginBottom: 14 }}>
            <button type="button" className={mode === "text" ? "on" : ""} onClick={() => setMode("text")}>Текст</button>
            <button type="button" className={mode === "url" ? "on" : ""} onClick={() => setMode("url")}>Ссылка</button>
            <button type="button" className={mode === "file" ? "on" : ""} onClick={() => setMode("file")}>Файл</button>
          </div>
          {mode === "file" ? (
            <>
              <label className="small muted" htmlFor="f">PDF, скан или фото документа (до 10 МБ)</label>
              <input
                id="f"
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.txt"
                style={{ display: "block", margin: "6px 0 10px" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return setFile(null);
                  if (f.size > 10 * 1024 * 1024) {
                    setError("Файл больше 10 МБ");
                    return setFile(null);
                  }
                  const reader = new FileReader();
                  reader.onload = () => setFile({ name: f.name, b64: String(reader.result).split(",")[1] || "" });
                  reader.readAsDataURL(f);
                }}
              />
              <p className="small muted">Текст из сканов распознаётся OCR (Tesseract), затем анализируется моделью.</p>
            </>
          ) : mode === "text" ? (
            <>
              <label className="small muted" htmlFor="t">Заголовок (необязательно)</label>
              <input id="t" className="input wide" value={title} onChange={(e) => setTitle(e.target.value)} style={{ margin: "4px 0 12px" }} />
              <label className="small muted" htmlFor="x">Текст новости</label>
              <textarea id="x" className="input" value={text} onChange={(e) => setText(e.target.value)} style={{ marginTop: 4 }} />
              <button type="button" className="btn ghost small" style={{ marginTop: 6 }} onClick={() => { setTitle(SAMPLE.title); setText(SAMPLE.text); }}>
                Подставить пример
              </button>
            </>
          ) : (
            <>
              <label className="small muted" htmlFor="u">Ссылка на публичную страницу новости</label>
              <input id="u" className="input wide" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" style={{ marginTop: 4 }} />
            </>
          )}
          <div style={{ marginTop: 16 }}>
            <button
              className="btn primary"
              disabled={busy || (mode === "text" ? text.trim().length < 40 : mode === "url" ? !url.trim() : !file)}
            >
              {busy ? "Агент работает…" : "Проанализировать"}
            </button>
          </div>
          {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
        </form>
        <div className="panel panel-pad">
          <h3>Ход анализа</h3>
          {steps.length === 0 && !busy && <p className="small muted" style={{ marginTop: 8 }}>Шаги агента появятся здесь во время анализа.</p>}
          <div className="progress" style={{ marginTop: 8 }} aria-live="polite">
            {steps.map((s, i) => (
              <div key={i}>
                <span className={`dot ${busy && i === steps.length - 1 ? "run" : ""}`} />
                <span>
                  <b>{STEP_LABEL[s.kind] || s.kind}:</b> {s.name}
                  {s.output_summary && <span className="muted"> — {s.output_summary}</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
      {result && (
        <div style={{ marginTop: 28 }}>
          <InsightView data={result} />
        </div>
      )}
    </div>
  );
}
