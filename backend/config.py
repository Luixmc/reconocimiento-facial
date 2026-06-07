"""
Configuración centralizada para el sistema de reconocimiento facial.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env desde el directorio del backend (un nivel arriba si se ejecuta desde raíz)
_env_path = Path(__file__).parent / ".env"
load_dotenv(_env_path)

# ─── Supabase ──────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
COMPANY_ID = os.environ["COMPANY_ID"]

# ─── Cámara ────────────────────────────────────────────────────────────────
DEFAULT_CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "1"))
CAPTURE_INTERVAL_SECONDS = 0.5  # Mínimo entre capturas para evitar duplicados

# ─── Detección facial ──────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.5    # Umbral de confianza para considerar match
MATCH_MINIMUM_SIMILARITY = 0.3  # Similitud mínima para considerar match
AUTHORIZED_SIMILARITY = 0.5     # Similitud mínima para autorizar acceso
MODEL_NAME = "insightface"
MODEL_VERSION = "0.7.3"

# ─── Servidor HTTP interno (para comunicación con Flutter) ──────────────────
FLASK_HOST = "127.0.0.1"
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5050"))

# ─── Rate Limiting (SaaS: evitar spam a Supabase) ──────────────────────────
CAPTURE_COOLDOWN_SECONDS = 3.0  # Mínimo entre capturas para la MISMA persona
FACE_LEAVE_TIMEOUT = 5.0         # Segundos sin rostro para considerar que "salió"
MIN_CONFIDENCE_TO_CAPTURE = 0.7  # Confianza mínima para considerar detección "perfecta"
# Frame skip: cuántos frames saltarse antes de ejecutar InsightFace.
# Con 15 cámaras simultáneas se recomienda 5-8 para reducir carga de CPU.
DETECTION_FRAME_SKIP = int(os.environ.get("FRAME_SKIP", "3"))

# ─── Dispositivo ──────────────────────────────────────────────────────────
# Versión de la app (usada para OTA y heartbeat)
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")

# ─── Kiosko ────────────────────────────────────────────────────────────────
KIOSK_MODE = os.environ.get("KIOSK_MODE", "0") == "1"
KIOSK_PIN  = os.environ.get("KIOSK_PIN", "1234")

# ─── Snapshot (calidad JPEG para /api/snapshot) ────────────────────────────
# Con muchas cámaras simultáneas reducir a 70 ahorra ~25% de ancho de banda.
SNAPSHOT_QUALITY = int(os.environ.get("SNAPSHOT_QUALITY", "85"))

# ─── Supabase Storage ──────────────────────────────────────────────────────
STORAGE_BUCKET = "face-snapshots"
