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


class EcmPickBody(BaseModel):
    kind: str = Field("xlsx", description="'xlsx' (single file) or 'folder'.")


class FsMkdirBody(BaseModel):
    """Create a new folder inside `path` (used by the in-browser file picker)."""
    path: str
    name: str


class EcmCapacityBody(BaseModel):
    path: str
    sheet: str = "Record List1"


class EcmExtractBody(BaseModel):
    path: str
    sheet: str = "Record List1"
    pulse_max_seconds: float = 60.0


class EcmFitBody(BaseModel):
    path: str
    sheet: str = "Record List1"
    rc_order: int = 1
    algorithm: str = "curve_fit"
    capacity: float | None = None
    pulse_max_seconds: float = 60.0
    # Optional limits. When omitted, detected HPPC-window ranges are used for the
    # voltage clip; current limits only drive warnings.
    v_max: float | None = None
    v_min: float | None = None
    i_chg_max: float | None = None
    i_dch_max: float | None = None
    nominal_capacity: float | None = None  # reference only; does not affect SOC
    ocv_mode: str = "both"  # "tabulated" | "analytical" | "both"
    ocv_poly_degree: int = 8
    # 0% SOC extrapolation technique; "none" disables the appended 0% row.
    zero_soc_method: str = "log_poly2"


class OcvComputeBody(BaseModel):
    """OCV-test computation (separate from the HPPC/ECM fit)."""
    path: str
    sheet: str = "Record List1"
    capacity: float | None = None  # SOC-axis capacity; defaults to detected Qd
    ocv_mode: str = "both"  # "tabulated" | "analytical" | "both"
    ocv_poly_degree: int = 8


# --------------------------------------------------------------------------- #
# Battery Life Prediction — RUL classification
# --------------------------------------------------------------------------- #
class RulPickBody(BaseModel):
    """File picker for the RUL tab.

    'file'   → a single .pkl or .npz cell file
    'folder' → a folder of cell files (batch)
    'ckpt'   → a checkpoint folder (model weights + scalers)
    """
    kind: str = "file"


class RulInspectBody(BaseModel):
    path: str  # .pkl or .npz


class RulPredictBody(BaseModel):
    path: str            # .pkl or .npz cell file
    ckpt_dir: str        # checkpoint folder (best_clf*.pt + scalers)
    has_full_history: bool = True
    query_cycle: int | None = None  # window end cycle; defaults to the latest
