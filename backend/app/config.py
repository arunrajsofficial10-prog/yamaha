import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
DATA_DIR = Path(os.environ.get("LAK_DATA_DIR") or ROOT_DIR / "data")
PROJECTS_DIR = DATA_DIR / "projects"

# Hosting. PUBLIC = the app is shared on the internet: each browser only sees its own songs, and
# uploads are deleted after RETENTION_DAYS so the server's disk doesn't fill up.
PUBLIC = os.environ.get("LAK_PUBLIC", "").lower() in ("1", "true", "yes")
RETENTION_DAYS = float(os.environ.get("LAK_RETENTION_DAYS", "3" if PUBLIC else "0"))  # 0 = keep forever

# Upload validation
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg"}
MAX_UPLOAD_BYTES = int(float(os.environ.get("LAK_MAX_UPLOAD_MB", "150")) * 1024 * 1024)
MIN_DURATION_SEC = 8.0           # shorter clips don't contain enough beats to estimate tempo reliably
MAX_DURATION_SEC = 20 * 60

# Analysis
ANALYSIS_SR = 22050              # all DSP runs at this sample rate
HOP_LENGTH = 512                 # ~23 ms frames at 22.05 kHz
BPM_MIN = 40.0
BPM_MAX = 220.0
LOW_CONFIDENCE = 0.6             # below this, the UI shows a review warning

# MIDI / style (Phase 5+)
DEFAULT_PPQ = 480
