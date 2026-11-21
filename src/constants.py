from os import environ
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(environ.get("DATA_DIR", BASE_DIR / "data"))

RAW_DIR = DATA_DIR / "raw"
CSV_DIR = DATA_DIR / "csv"
RERUN_DIR = DATA_DIR / "rerun"
DBC_DIR = DATA_DIR / "DBCFiles"
CLASSIFICATIONS_FILE = DATA_DIR / "vehicle_classifications.json"
