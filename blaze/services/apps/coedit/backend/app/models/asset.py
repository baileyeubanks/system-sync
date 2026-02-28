from pydantic import BaseModel
from typing import Optional, List


class AssetResponse(BaseModel):
    id: str
    project_id: str
    folder_id: Optional[str] = None
    name: str
    asset_type: str
    status: str
    created_by: str
    created_at: float
    updated_at: float
    versions: List["VersionResponse"] = []


class VersionResponse(BaseModel):
    id: str
    asset_id: str
    version_num: int
    thumbnail_path: Optional[str] = None
    sprite_path: Optional[str] = None
    file_size: Optional[int] = None
    duration_ms: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    codec: Optional[str] = None
    transcode_job_id: Optional[str] = None
    created_at: float


class UploadInitResponse(BaseModel):
    upload_id: str
    asset_id: str
    version_id: str
    transcode_job_id: Optional[str] = None


class AssetUpdate(BaseModel):
    name: Optional[str] = None


class AssetMove(BaseModel):
    folder_id: Optional[str] = None


class BatchAction(BaseModel):
    asset_ids: List[str]


class BatchMove(BaseModel):
    asset_ids: List[str]
    folder_id: Optional[str] = None


AssetResponse.model_rebuild()
