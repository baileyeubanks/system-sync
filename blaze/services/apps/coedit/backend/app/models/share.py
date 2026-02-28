from pydantic import BaseModel
from typing import Optional, List, Any


class ShareCreate(BaseModel):
    mode: str  # review, approval, preview
    password: Optional[str] = None
    allow_download: bool = False
    expires_days: Optional[int] = None


class ShareResponse(BaseModel):
    id: str
    asset_id: Optional[str] = None
    version_id: Optional[str] = None
    mode: str
    token: str
    url: str
    allow_download: bool
    has_password: bool = False
    expires_at: Optional[float] = None
    created_at: float


class ShareValidate(BaseModel):
    password: Optional[str] = None


class ReviewSession(BaseModel):
    asset_id: str
    asset_name: str
    version_id: str
    version_num: int
    mode: str
    allow_download: bool
    fps: Optional[float] = None
    duration_ms: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class ApprovalCreate(BaseModel):
    reviewer_name: str
    status: str  # approved, changes_requested
    note: Optional[str] = None


class ApprovalResponse(BaseModel):
    id: str
    asset_id: str
    version_id: str
    share_link_id: Optional[str] = None
    reviewer_name: str
    status: str
    note: Optional[str] = None
    created_at: float


# Project-level sharing
class ProjectShareCreate(BaseModel):
    mode: str  # review, approval, preview
    password: Optional[str] = None
    allow_download: bool = False
    expires_days: Optional[int] = None


class ProjectShareResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    mode: str
    token: str
    url: str
    allow_download: bool
    has_password: bool = False
    expires_at: Optional[float] = None
    created_at: float


class ProjectReviewSession(BaseModel):
    project_id: str
    project_name: str
    mode: str
    allow_download: bool
    assets: List[Any]
