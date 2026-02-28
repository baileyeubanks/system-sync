from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ---- Learning ----

class LearningSourceRequest(BaseModel):
    business_unit: str = "CC"
    source_type: str
    source_ref: str
    title: Optional[str] = None
    metadata: Optional[dict] = None
    active: bool = True


class LearningItemRequest(BaseModel):
    business_unit: str = "CC"
    source_type: str
    title: str
    source_ref: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[str] = None
    transcript_text: Optional[str] = None
    summary_text: Optional[str] = None
    relevance_score: float = 0.0
    tags: Optional[List[str]] = None
    idempotency_key: Optional[str] = None
    source_id: Optional[int] = None


class LearningInsightRequest(BaseModel):
    business_unit: str = "CC"
    insight_type: str
    title: str
    insight_text: str
    confidence: float = 0.5
    priority: int = 3
    learning_item_id: Optional[int] = None
    contact_id: Optional[int] = None
    tags: Optional[List[str]] = None
    status: str = "active"


# ---- Outreach ----

class OutreachDraftRequest(BaseModel):
    business_unit: str = "CC"
    channel: str
    recipient: str
    body_text: str
    subject: Optional[str] = None
    rationale: Optional[str] = None
    contact_id: Optional[int] = None
    source_insight_ids: List[int] = Field(default_factory=list)


# ---- Voice ----

class VoiceTranscribeRequest(BaseModel):
    business_unit: str = "CC"
    audio_base64: Optional[str] = None
    text_hint: Optional[str] = None
    idempotency_key: Optional[str] = None


class VoiceSpeakRequest(BaseModel):
    business_unit: str = "CC"
    text: str
    voice_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    intent: str = "speak"


# ---- Integrations ----

class XLogUsageRequest(BaseModel):
    amount_usd: float = 0.0


class GoogleSmokeRequest(BaseModel):
    delegated_subject: Optional[str] = None
    business_unit: Optional[str] = None


class IntegrationSmokeRequest(BaseModel):
    delegated_subject: Optional[str] = None


# ---- ACS ----

class AcsJobAssignRequest(BaseModel):
    crew_member_name: str
    role: Optional[str] = None


class AcsJobStatusRequest(BaseModel):
    status: str


class AcsReminderPreviewRequest(BaseModel):
    lead_minutes: int = 30


class AcsCrewLocationSyncRequest(BaseModel):
    events: List[Any] = Field(default_factory=list)
    provider: str = "traccar"
