import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.db.models import Company, Event, Material
from app.schemas import TraceStep

LIMITS = {"tool_calls": 12, "llm_calls": 8, "tokens": 30000, "adapt_iterations": 2}


class LimitExceeded(Exception):
    pass


@dataclass
class RunContext:
    event: Event
    company: Company
    role: str
    user: str | None
    relevance: dict
    trace_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:10]}")
    materials: dict[str, Material] = field(default_factory=dict)
    alfa: dict[str, Material] = field(default_factory=dict)
    related: list[dict] = field(default_factory=list)
    counterparties: list[dict] = field(default_factory=list)
    group: dict | None = None
    products: list[dict] = field(default_factory=list)
    exposure: list[dict] = field(default_factory=list)
    search_hits: list[dict] = field(default_factory=list)
    plan: dict | None = None
    steps: list[TraceStep] = field(default_factory=list)
    tool_calls: int = 0
    llm_calls: int = 0
    tokens: int = 0
    cost_rub: float = 0.0
    models: dict = field(default_factory=dict)
    started: datetime = field(default_factory=datetime.utcnow)
    notes: list[str] = field(default_factory=list)
    on_step: Callable | None = None

    @property
    def profile(self) -> dict:
        return self.company.profile

    def step(
        self,
        kind: str,
        name: str,
        *,
        input_summary: str = "",
        output_summary: str = "",
        model: str | None = None,
        tokens: int = 0,
        cost_rub: float = 0.0,
        ms: int = 0,
        ok: bool = True,
        detail: dict | None = None,
    ) -> TraceStep:
        s = TraceStep(
            n=len(self.steps) + 1,
            kind=kind,
            name=name,
            input_summary=input_summary[:400],
            output_summary=output_summary[:600],
            model=model,
            tokens=tokens,
            cost_rub=cost_rub,
            ms=ms,
            ok=ok,
            detail=detail or {},
        )
        self.steps.append(s)
        if self.on_step:
            try:
                self.on_step(s)
            except Exception:
                pass
        return s

    def charge_llm(self, meta) -> None:
        self.llm_calls += 1
        self.tokens += meta.tokens
        self.cost_rub += meta.cost_rub
        if self.llm_calls > LIMITS["llm_calls"] or self.tokens > LIMITS["tokens"]:
            raise LimitExceeded("run budget exhausted")


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.perf_counter() - self.t0) * 1000)
