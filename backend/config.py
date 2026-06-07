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
# Service role: el backend es un servicio de confianza (escanea/escribe ya
# filtrando por COMPANY_ID), así que usa esta key para saltar RLS en sus
# llamadas REST/Storage en lugar de la publishable key (sujeta a RLS de panel).
SERVICE_KEY = os.environ["SERVICE_KEY"]
COMPANY_ID = os.environ["COMPANY_ID"]

# ─── Cámara ────────────────────────────────────────────────────────────────
DEFAULT_CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "1"))
CAPTURE_INTERVAL_SECONDS = 0.5  # Mínimo entre capturas para evitar duplicados

# ─── Detección facial ──────────────────────────────────────────────────────
# Umbrales calibrados para embeddings ArcFace de buffalo_l (coseno).
# Para personas distintas el coseno suele caer < 0.3; misma persona > 0.45.
CONFIDENCE_THRESHOLD = 0.5    # Umbral de confianza para considerar match
MATCH_MINIMUM_SIMILARITY = 0.35  # Similitud mínima para considerar match (candidato)
AUTHORIZED_SIMILARITY = 0.45     # Similitud mínima para AUTORIZAR el acceso
# Margen mínimo entre el mejor match y la 2ª persona más parecida. Si dos
# personas distintas quedan más cerca que esto, el match es ambiguo → no autoriza.
AMBIGUITY_MARGIN = 0.08
# Ancho mínimo del rostro (px, en coords originales) para intentar reconocer.
# Rostros más pequeños están demasiado lejos y producen falsos positivos.
MIN_FACE_PIXELS = 70
MODEL_NAME = "insightface"
MODEL_VERSION = "0.7.3"

# ─── Servidor HTTP interno (para comunicación con Flutter) ──────────────────
FLASK_HOST = "127.0.0.1"
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5050"))

# ─── Rate Limiting (SaaS: evitar spam a Supabase) ──────────────────────────
CAPTURE_COOLDOWN_SECONDS = 3.0  # Mínimo entre capturas para la MISMA persona
# Personas YA AUTORIZADAS: cooldown más largo para no llenar el historial de
# accesos con registros repetidos de alguien que se queda parado frente a la cámara.
AUTHORIZED_CAPTURE_COOLDOWN_SECONDS = 30.0
FACE_LEAVE_TIMEOUT = 5.0         # Segundos sin rostro para considerar que "salió"
MIN_CONFIDENCE_TO_CAPTURE = 0.7  # Confianza mínima para considerar detección "perfecta"
# Frame skip: cuántos frames saltarse antes de ejecutar InsightFace.
# Priorizamos fluidez/fiabilidad sobre uso de recursos: detectar en cada frame.
DETECTION_FRAME_SKIP = int(os.environ.get("FRAME_SKIP", "1"))
# Tiempo máximo intentando reconocer un rostro antes de declararlo "no registrado"
# y mostrar la guía correspondiente en pantalla (la persona sigue sin registrarse).
UNKNOWN_FACE_TIMEOUT_SECONDS = 7.0

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
