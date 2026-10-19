export type ClaimKind = "fact" | "bank" | "ai";

export interface SourceRef {
  type: "material" | "alfa" | "feature" | "claim" | "event";
  id: string;
  quote?: string | null;
  label?: string | null;
}

export interface Claim {
  id: string;
  kind: ClaimKind;
  text: string;
  sources: SourceRef[];
  factor?: string | null;
  bank_product?: string | null;
  bank_product_title?: string | null;
}

export interface InsightBody {
  insight_id: string;
  event_id: string;
  title: string;
  summary: string;
  relevance: Record<string, any>;
  sections: {
    what_happened: Claim[];
    why_it_matters: Claim[];
    consequences: { risks: Claim[]; opportunities: Claim[]; watch_factors: Claim[] };
    actions: Claim[];
    bank_view: Claim[];
    evidence: { features: string[]; materials: string[]; alfa: string[] };
  };
  factors: string[];
  polarity: "risk" | "opportunity" | "mixed";
  horizon: string;
  confidence: number;
  verification: { passed: boolean; iterations: number; issues: any[]; checks: any[] };
  status: "ok" | "insufficient_data" | "not_relevant" | "failed";
  missing: string[];
  trace_id: string;
  generated_at: string;
  models: Record<string, string>;
}

export interface SourceMeta {
  id: string;
  kind: "news" | "alfa";
  source: string;
  title: string;
  url?: string | null;
  report?: string | null;
  page?: number | null;
  synthetic: boolean;
  published_at?: string | null;
}

export interface InsightPayload {
  insight: InsightBody;
  status: string;
  stale: boolean;
  score: number;
  event: { id: string; title: string; classification: any; published_at?: string; private: boolean };
  factor_names: Record<string, string>;
  sources: Record<string, SourceMeta>;
}

export interface FeedCard {
  event_id: string;
  insight_id: string | null;
  generated: boolean;
  title: string;
  event_title: string;
  summary: string;
  polarity: "risk" | "opportunity" | "mixed";
  status: string;
  score: number | null;
  factors: { code: string; name: string }[];
  horizon: string | null;
  confidence: number | null;
  has_bank_view: boolean;
  published_at: string | null;
  sources: { id: string; source: string }[];
  private: boolean;
  type: string;
  regions: string[];
  stale: boolean;
}

export interface Company {
  id: string;
  industry: string;
  okved: string;
  sector: string;
  region: string;
  city: string;
  revenue_rub: number | null;
  is_large: boolean;
  client_type: string;
  group_id: string | null;
}

export interface User {
  login: string;
  display_name: string;
  role: string;
  role_name: string;
  role_focus: string;
  is_admin: boolean;
  company: Company;
}

const TOKEN_KEY = "pulse_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api/v1${path}`, { ...init, headers: { ...headers, ...(init.headers as any) } });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = data?.error || {};
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, e.code || "error", e.message || `Ошибка ${res.status}`);
  }
  return data as T;
}

export const api = {
  users: () => request<User[]>("/auth/users"),
  login: (login: string) =>
    request<{ token: string; user: User }>("/auth/login", { method: "POST", body: JSON.stringify({ login }) }),
  me: () => request<User>("/me"),
  meta: () => request<{ factors: Record<string, string>; roles: Record<string, string>; llm_mode: string }>("/meta"),
  widget: () => request<{ top: FeedCard[]; total: number; new_count: number; risk_count: number; as_of: string }>("/home/widget"),
  feed: (params: Record<string, string>) =>
    request<{ items: FeedCard[]; total: number; page: number; page_size: number }>(
      "/feed?" + new URLSearchParams(params).toString(),
    ),
  digest: () => request<{ text: string; items: { insight_id: string; title: string; polarity: string }[] }>("/digest"),
  insight: (id: string) => request<InsightPayload>(`/insights/${id}`),
  eventInsight: (eventId: string) => request<InsightPayload>(`/events/${eventId}/insight`),
  regenerate: (id: string) => request<InsightPayload>(`/insights/${id}/regenerate`, { method: "POST" }),
  trace: (id: string) => request<any>(`/insights/${id}/trace`),
  material: (id: string) => request<any>(`/materials/${id}`),
  profile: () => request<any>("/companies/me/profile"),
  chat: (message: string, insight_id?: string, history: any[] = []) =>
    request<{ text: string; claims: Claim[]; mode: string }>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, insight_id, history }),
    }),
  feedback: (insight_id: string, vote: 1 | -1, reason?: string, comment?: string) =>
    request<void>("/feedback", { method: "POST", body: JSON.stringify({ insight_id, vote, reason, comment }) }),
  uiEvent: (kind: string, ref?: string) =>
    request<void>("/ui-events", { method: "POST", body: JSON.stringify({ kind, ref }) }).catch(() => undefined),
  metrics: () => request<any>("/admin/metrics"),
};

export async function analyzeStream(
  body: { text?: string; url?: string; title?: string },
  onStep: (step: any) => void,
): Promise<InsightPayload> {
  const token = getToken();
  const res = await fetch("/api/v1/analyze?stream=true", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data?.error?.code || "error", data?.error?.message || `Ошибка ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: InsightPayload | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = /event: (\w+)/.exec(chunk)?.[1];
      const dataLine = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!ev || !dataLine) continue;
      const data = JSON.parse(dataLine.slice(6));
      if (ev === "step") onStep(data);
      if (ev === "result") result = data;
      if (ev === "error") throw new ApiError(400, data.code, data.message);
    }
  }
  if (!result) throw new ApiError(500, "no_result", "Анализ не вернул результат");
  return result;
}

export const fmt = {
  date(iso?: string | null) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  },
  money(v?: number | null) {
    if (!v) return "н/д";
    return `${(v / 1e9).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млрд ₽`;
  },
  horizon(h?: string | null) {
    return ({ "0-1m": "до 1 месяца", "0-3m": "до 3 месяцев", "1-3m": "1–3 месяца", "3-12m": "3–12 месяцев" } as any)[h || ""] || h || "";
  },
  pct(v?: number | null) {
    return v == null ? "—" : `${Math.round(v * 100)}%`;
  },
};
