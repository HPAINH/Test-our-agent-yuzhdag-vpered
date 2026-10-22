from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    is_large: Mapped[bool] = mapped_column(Boolean, default=False)
    group_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    okved: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    revenue_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompanyFactor(Base):
    __tablename__ = "company_factor"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    factor: Mapped[str] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(Float)
    polarity: Mapped[str] = mapped_column(String(16))
    drivers: Mapped[list] = mapped_column(JSON, default=list)


class CompanyLink(Base):
    __tablename__ = "company_link"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src: Mapped[str] = mapped_column(String(64), index=True)
    dst: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float, default=0)
    count: Mapped[int] = mapped_column(Integer, default=0)
    via: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AppUser(Base):
    __tablename__ = "app_user"
    login: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"))
    role: Mapped[str] = mapped_column(String(32))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class Material(Base):
    __tablename__ = "material"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    report: Mapped[str | None] = mapped_column(String(256), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    event_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Event(Base):
    __tablename__ = "event"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    classification: Mapped[dict] = mapped_column(JSON, default=dict)
    facts: Mapped[list] = mapped_column(JSON, default=list)
    alfa_refs: Mapped[list] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    private_to: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Relevance(Base):
    __tablename__ = "relevance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)


class Insight(Base):
    __tablename__ = "insight"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class Trace(Base):
    __tablename__ = "trace"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_rub: Mapped[float] = mapped_column(Float, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class LLMCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_rub: Mapped[float] = mapped_column(Float, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(8), default="mock")


class LLMCache(Base):
    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    insight_id: Mapped[str] = mapped_column(String(64), index=True)
    user: Mapped[str] = mapped_column(String(64))
    vote: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UIEvent(Base):
    __tablename__ = "ui_event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    args_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class LaunchNonce(Base):
    __tablename__ = "launch_nonce"
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    exp: Mapped[datetime] = mapped_column(DateTime, index=True)


class UserCompany(Base):
    __tablename__ = "user_company"
    login: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class HiddenEvent(Base):
    __tablename__ = "hidden_event"
    login: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ServiceRequest(Base):
    __tablename__ = "service_request"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    login: Mapped[str] = mapped_column(String(64), index=True)
    company_id: Mapped[str] = mapped_column(String(64), index=True)
    insight_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    product: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new")
