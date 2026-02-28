from pydantic import BaseModel
from typing import Optional, List


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    branding: Optional[str] = None
    accent_color: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    owner_id: str
    branding: Optional[str] = None
    logo_path: Optional[str] = None
    accent_color: Optional[str] = '#3b82f6'
    created_at: float
    updated_at: float
    asset_count: int = 0


class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None


class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None
    sort_order: Optional[int] = None


class FolderResponse(BaseModel):
    id: str
    project_id: str
    parent_id: Optional[str] = None
    name: str
    sort_order: int = 0
    created_at: float
