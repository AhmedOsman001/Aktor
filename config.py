from pathlib import Path

APP_NAME = "FlowRecord"
DATA_DIR = Path.home() / "AppData" / "Roaming" / APP_NAME
DB_PATH = DATA_DIR / "flowrecord.db"

DEFAULT_RECORD_HOTKEY = "ctrl+shift+r"
MAX_DELAY_SECONDS = 5.0
MIN_ACTION_INTERVAL_MS = 50
DELAY_THRESHOLD = 0.3
APP_POLL_INTERVAL_MS = 500

DATA_DIR.mkdir(parents=True, exist_ok=True)
