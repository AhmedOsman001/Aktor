from pathlib import Path

APP_NAME = "FlowRecord"
DATA_DIR = Path.home() / "AppData" / "Roaming" / APP_NAME
DB_PATH = DATA_DIR / "flowrecord.db"

LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "flowrecord.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 5

DEFAULT_RECORD_HOTKEY = "ctrl+shift+r"
MAX_DELAY_SECONDS = 5.0
MIN_ACTION_INTERVAL_MS = 50
DELAY_THRESHOLD = 0.3
APP_POLL_INTERVAL_MS = 500

DATA_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROCESSES = frozenset({
    "explorer.exe", "svchost.exe", "runtimebroker.exe", "dllhost.exe",
    "audiodg.exe", "conhost.exe", "wmiprvse.exe", "backgroundtaskhost.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "dwm.exe", "csrss.exe", "lsass.exe", "services.exe",
    "wininit.exe", "winlogon.exe", "smss.exe", "fontdrvhost.exe", "sihost.exe",
    "taskhostw.exe", "ngciso.exe", "ctfmon.exe", "crrtexport.exe",
    "systemsettings.exe", "applicationframehost.exe",
})
