from pydantic import BaseModel
from typing import Optional, List


class CommentCreate(BaseModel):
    body: str
    frame_start: Optional[int] = None
    frame_end: Optional[int] = None
    timecode_start: Optional[str] = None
    timecode_end: Optional[str] = None
    pin_x: Optional[float] = None
    pin_y: Optional[float] = None
    annotation_type: Optional[str] = None
    annotation_data: Optional[str] = None
    parent_id: Optional[str] = None
    is_private: bool = False
    author_name: Optional[str] = None


class CommentUpdate(BaseModel):
    body: Optional[str] = None
    is_resolved: Optional[bool] = None


class CommentResponse(BaseModel):
    id: str
    asset_id: str
    version_id: str
    parent_id: Optional[str] = None
    author_id: Optional[str] = None
    author_name: str
    frame_start: Optional[int] = None
    frame_end: Optional[int] = None
    timecode_start: Optional[str] = None
    timecode_end: Optional[str] = None
    pin_x: Optional[float] = None
    pin_y: Optional[float] = None
    annotation_type: Optional[str] = None
    annotation_data: Optional[str] = None
    body: str
    is_private: bool = False
    is_resolved: bool = False
    created_at: float
    updated_at: float
    replies: List["CommentResponse"] = []


CommentResponse.model_rebuild()
