"""Shared paths and constants for the Battery AI Analyzer web app."""

from pathlib import Path

WEBAPP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEBAPP_DIR.parent

STATIC_DIR = WEBAPP_DIR / "UI"
DEFAULT_DATA_DIR = PROJECT_ROOT / "DATA" / "BatteryML"
DATA_INFO_DIR = PROJECT_ROOT / "Data_Info"

CACHE_DIR = WEBAPP_DIR / "cache"
CACHE_FILE = CACHE_DIR / "folder_cycle_cache.json"
CACHE_VERSION = 6
