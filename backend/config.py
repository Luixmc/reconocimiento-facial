"""
Configuración centralizada para el sistema de reconocimiento facial.
"""
import os

# ─── Supabase ──────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://gumkpfyrgctrgemqihxl.supabase.co")
SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd1bWtwZnlyZ2N0cmdlbXFpaHhsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDE4MDg4OCwiZXhwIjoyMDk1NzU2ODg4fQ.VSl8adBIBvvRs268J7KnK9Ku3L0yl1hXIVDjU4-MTwg",
)

COMPANY_ID = os.getenv("COMPANY_ID", "29a16860-65aa-4279-a924-9cc6b10443d8")

# ─── Cámara ────────────────────────────────────────────────────────────────
DEFAULT_CAMERA_INDEX = 1
CAPTURE_INTERVAL_SECONDS = 0.5  # Mínimo entre capturas para evitar duplicados

# ─── Detección facial ──────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.5    # Umbral de confianza para considerar match
MATCH_MINIMUM_SIMILARITY = 0.3  # Similitud mínima para considerar match
AUTHORIZED_SIMILARITY = 0.5     # Similitud mínima para autorizar acceso
MODEL_NAME = "insightface"
MODEL_VERSION = "0.7.3"

# ─── Servidor HTTP interno (para comunicación con Flutter) ──────────────────
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5050

# ─── Rate Limiting (SaaS: evitar spam a Supabase) ──────────────────────────
CAPTURE_COOLDOWN_SECONDS = 3.0  # Mínimo entre capturas para la MISMA persona
FACE_LEAVE_TIMEOUT = 5.0         # Segundos sin rostro para considerar que "salió"
MIN_CONFIDENCE_TO_CAPTURE = 0.7  # Confianza mínima para considerar detección "perfecta"
DETECTION_FRAME_SKIP = 3         # Ejecutar detección cada N frames

# ─── Supabase Storage ──────────────────────────────────────────────────────
STORAGE_BUCKET = "face-snapshots"
