"""Request body models for API endpoints."""

from pydantic import BaseModel, Field


class LoadPathBody(BaseModel):
    path: str = Field(..., description="Path to a .pkl on the server machine.")


class FolderPathBody(BaseModel):
    path: str = Field(..., description="Path to a folder on the server machine.")


class PlotBody(BaseModel):
    kind: str
    cycles: str | None = None
    min_dv: float = 1e-4
    min_dq: float = 1e-5
    filter_outliers: bool = False


class FeaturePlotBody(BaseModel):
    kind: str
    cycles: str | None = None
    reference_cycle: int = 0
    use_reference_cycle: bool = True
    min_dv: float = 1e-4
    min_dq: float = 1e-5
    filter_outliers: bool = False
    compare_session_id: str | None = None


class FolderLogavgBody(BaseModel):
    folder_path: str
    kind: str
    reference_cycle: int = 0
    use_reference_cycle: bool = True
    target_cycle: int = 1
    min_dv: float = 1e-4
    min_dq: float = 1e-5
    filter_outliers: bool = False


class ScreenshotBody(BaseModel):
    filename: str
    data: str
