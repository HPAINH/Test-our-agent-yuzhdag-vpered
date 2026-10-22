from typing import Literal

from pydantic import BaseModel, Field, model_validator

ClaimKind = Literal["fact", "bank", "ai"]
SourceType = Literal["material", "alfa", "feature", "claim", "event"]
InsightStatus = Literal["ok", "insufficient_data", "not_relevant", "failed"]
Direction = Literal["up", "down", "flat"]
Polarity = Literal["risk", "opportunity", "mixed"]


class SourceRef(BaseModel):
    type: SourceType
    id: str
    quote: str | None = None
    label: str | None = None


class Claim(BaseModel):
    id: str
    kind: ClaimKind
    text: str
    sources: list[SourceRef] = Field(min_length=1)
    factor: str | None = None
    bank_product: str | None = None
    bank_product_title: str | None = None

    @model_validator(mode="after")
    def _kind_matches_sources(self) -> "Claim":
        types = {s.type for s in self.sources}
        if self.kind == "fact" and not types & {"material", "event"}:
            raise ValueError(f"claim {self.id}: fact requires a material source")
        if self.kind == "bank" and "alfa" not in types:
            raise ValueError(f"claim {self.id}: bank requires an alfa source")
        if self.kind == "ai" and not types & {"feature", "claim", "material", "alfa"}:
            raise ValueError(f"claim {self.id}: ai requires feature or claim source")
        return self


class Consequences(BaseModel):
    risks: list[Claim] = []
    opportunities: list[Claim] = []
    watch_factors: list[Claim] = []


class Evidence(BaseModel):
    features: list[str] = []
    materials: list[str] = []
    alfa: list[str] = []


class Sections(BaseModel):
    what_happened: list[Claim] = []
    why_it_matters: list[Claim] = []
    consequences: Consequences = Consequences()
    actions: list[Claim] = []
    bank_view: list[Claim] = []
    evidence: Evidence = Evidence()


class Verification(BaseModel):
    passed: bool
    iterations: int
    issues: list[dict] = []
    checks: list[dict] = []


class InsightBody(BaseModel):
    insight_id: str
    event_id: str
    company_id: str
    role: str
    title: str
    summary: str = ""
    relevance: dict = {}
    sections: Sections
    factors: list[str] = []
    polarity: Polarity = "mixed"
    horizon: str = "1-3m"
    confidence: float = Field(ge=0, le=1)
    verification: Verification
    status: InsightStatus
    missing: list[str] = []
    trace_id: str
    generated_at: str
    models: dict = {}

    def all_claims(self) -> list[Claim]:
        s = self.sections
        return [
            *s.what_happened,
            *s.why_it_matters,
            *s.consequences.risks,
            *s.consequences.opportunities,
            *s.consequences.watch_factors,
            *s.actions,
            *s.bank_view,
        ]


class FactorEffect(BaseModel):
    factor: str
    direction: Direction = "flat"
    strength: int = Field(ge=1, le=3, default=1)


class EventClassification(BaseModel):
    type: Literal[
        "monetary", "fx", "commodity", "regulation", "sanctions", "industry", "company", "macro_stat", "other"
    ] = "other"
    factors: list[FactorEffect] = []
    okved_sections: list[str] = []
    regions: list[str] = []
    participants: list[str] = []
    dates: list[str] = []
    modality: Literal["fact", "forecast", "opinion"] = "fact"
    method: str = "rules"
    flags: list[str] = []


class EventFact(BaseModel):
    text: str
    quote: str
    material_id: str


class PlanStep(BaseModel):
    n: int
    tool: str
    args: dict = {}
    why: str = ""


class AgentPlan(BaseModel):
    goal: str
    steps: list[PlanStep]
    expected_outputs: list[str] = []


class TraceStep(BaseModel):
    n: int
    kind: Literal["plan", "tool", "llm", "verify", "retry", "finalize", "error"]
    name: str
    input_summary: str = ""
    output_summary: str = ""
    model: str | None = None
    tokens: int = 0
    cost_rub: float = 0
    ms: int = 0
    ok: bool = True
    detail: dict = {}


class LoginIn(BaseModel):
    login: str


class AnalyzeIn(BaseModel):
    text: str | None = Field(default=None, max_length=20000)
    url: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=500)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    insight_id: str | None = None
    history: list[dict] = Field(default_factory=list, max_length=20)


class FeedbackIn(BaseModel):
    insight_id: str
    vote: Literal[1, -1]
    reason: Literal["irrelevant", "factual_error", "obvious", "no_action", "useful", "other"] | None = None
    comment: str | None = Field(default=None, max_length=1000)


class UIEventIn(BaseModel):
    kind: Literal["open_source", "open_insight", "open_trace", "filter", "chat_quick"]
    ref: str | None = None


class LaunchIn(BaseModel):
    launch_token: str = Field(min_length=1, max_length=2048)


class LaunchLinkIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=128)
