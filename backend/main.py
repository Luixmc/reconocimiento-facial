"""
Servidor principal del sistema de reconocimiento facial (headless).
- Captura video de cámara
- Detecta rostros con InsightFace (cada N frames para rendimiento)
- SOLO captura 1 foto por detección perfecta (rate limiting anti-spam)
- Sube snapshot a Supabase Storage
- Reconoce a la persona contra embeddings registrados
- Registra access_record + attendance + attendance_mark en la BD
- Expone API HTTP para Flutter (UI única)
- Sin ventanas OpenCV — todo se ve en la app Flutter
"""
from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import shutil
from flask import Flask

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from source_manager import SourceManager, SourceConfig
from config import (
    APP_VERSION,
    AUTHORIZED_CAPTURE_COOLDOWN_SECONDS,
    AUTHORIZED_SIMILARITY,
    COMPANY_ID,
    DEFAULT_CAMERA_INDEX,
    DETECTION_FRAME_SKIP,
    FACE_LEAVE_TIMEOUT,
    CAPTURE_COOLDOWN_SECONDS,
    MATCH_MINIMUM_SIMILARITY,
    MIN_CONFIDENCE_TO_CAPTURE,
    FLASK_HOST,
    FLASK_PORT,
    SNAPSHOT_QUALITY,
    UNKNOWN_FACE_TIMEOUT_SECONDS,
)
from device_manager import DeviceManager, get_or_create_device_uid
from offline_queue import OfflineQueue

# Identificador único de instancia — permite correr múltiples backends en
# puertos distintos sin que sus archivos de runtime se pisen entre sí.
_INSTANCE_ID = os.environ.get("INSTANCE_ID", str(FLASK_PORT))
from api_routes import register_routes
from attendance_service import handle_attendance
from enroll_service import EnrollService
from face_detector import FaceDetector
from remote_logger import attach_remote_logging
from startup_checks import run_startup_checks
from supabase_client import AccessRecord, SupabaseClient

# ── Logging con rotación ──────────────────────────────────────────────────
from logging.handlers import RotatingFileHandler

log_file = Path(BASE_DIR / f"backend_{_INSTANCE_ID}.log")
# Rotar cada 5MB, mantener 3 backups
handler_file = RotatingFileHandler(
    log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
handler_file.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
))
handler_console = logging.StreamHandler()
handler_console.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
))

logging.basicConfig(level=logging.INFO, handlers=[handler_file, handler_console])
logger = logging.getLogger("backend")

# ── Estado global ────────────────────────────────────────────────────────
_state = {
    "status": "starting",
    "camera_index": DEFAULT_CAMERA_INDEX,
    "camera_name": f"Camara {DEFAULT_CAMERA_INDEX}",
    "source_type": "usb",
    "source_url": None,
    "last_detection": None,
    "last_snapshot_url": None,
    "last_person_name": None,
    # Estado de reconocimiento EN VIVO (se actualiza en cada frame analizado,
    # independientemente de si la detección se persiste o no). Lo consume la UI
    # para mostrar mensajes/guías ("Reconociendo...", "No registrado", etc).
    "recognition": {
        "has_face": False,
        "phase": "idle",          # idle | detecting | recognized | low_confidence | unknown
        "person_name": None,
        "similarity": 0.0,
        "confidence": 0.0,
        "quality": None,          # alta | media | baja
        "seconds_elapsed": 0.0,
        "max_wait_seconds": UNKNOWN_FACE_TIMEOUT_SECONDS,
    },
    "fps": 0,
    "total_detections": 0,
    # SaaS extras (device_uid se actualiza en main() tras inicializar _DEVICE_UID)
    "supabase_online": False,
    "device_uid": "",
    "offline_pending": 0,
    "app_version": APP_VERSION,
}
_state_lock = threading.Lock()

_source = SourceManager()
_supabase = SupabaseClient()
_detector = FaceDetector(_supabase)

# ── Módulos SaaS ─────────────────────────────────────────────────────────
# IMPORTANTE: inicializar _DEVICE_UID antes de _state para poder referenciarlo
_DEVICE_UID = get_or_create_device_uid()
_device_mgr = DeviceManager(_supabase, _DEVICE_UID, COMPANY_ID, APP_VERSION)
_offline_q = OfflineQueue()

_capture_running = threading.Event()
_shutdown = threading.Event()  # Señal de shutdown para el watchdog

# ── Estado interno de rate-limiting ──────────────────────────────────────
_last_capture_time: float = 0.0
_last_person_id: str | None = None      # última persona detectada
_last_person_time: float = 0.0          # cuándo se detectó por última vez
_last_face_present_time: float = 0.0    # cuándo se vio un rostro por última vez
_face_seen_since: float | None = None    # cuándo apareció el rostro actual (para el contador de 7s)


def _quality_label(similarity: float) -> str | None:
    """Etiqueta de calidad del match para mostrar en la guía de pantalla."""
    if similarity <= 0:
        return None
    if similarity >= AUTHORIZED_SIMILARITY:
        return "alta"
    if similarity >= MATCH_MINIMUM_SIMILARITY:
        return "media"
    return "baja"


def _idle_recognition_state() -> dict:
    return {
        "has_face": False,
        "phase": "idle",
        "person_name": None,
        "similarity": 0.0,
        "confidence": 0.0,
        "quality": None,
        "seconds_elapsed": 0.0,
        "max_wait_seconds": UNKNOWN_FACE_TIMEOUT_SECONDS,
    }

# ── Buffer de frames para hilo lector de cámara ─────────────────────────
_frame_buffer: queue.Queue = queue.Queue(maxsize=2)
_last_frame_time: float = 0.0

# ── Caché JPEG para /api/snapshot ────────────────────────────────────────
# El hilo lector encoda una vez; los N requests HTTP por segundo lo leen sin encode.
# Dict mutable: api_routes y _camera_reader comparten la misma referencia.
_jpeg_shared: dict = {"frame": None}   # key "frame" → bytes | None
_jpeg_lock = threading.Lock()


app = Flask(__name__)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# ── Token de autenticación local ──────────────────────────────────────────
_TOKEN_FILE = BASE_DIR / f"backend_{_INSTANCE_ID}.token"
_BACKEND_TOKEN = secrets.token_hex(32)
_PUBLIC_ENDPOINTS = {"/api/health", "/api/snapshot", "/api/shutdown"}

# Las rutas se registran en main() una vez que los globals están listos


# --------------------------------------------------------------------------
# Limpieza de caché y archivos temporales
# --------------------------------------------------------------------------

def cleanup_cache() -> float:
    """
    Limpia archivos temporales del directorio backend.
    Devuelve el espacio liberado en MB.
    """
    freed_bytes = 0

    # __pycache__ (bytecode Python — se regenera solo)
    pycache = BASE_DIR / "__pycache__"
    if pycache.exists():
        try:
            for f in pycache.iterdir():
                freed_bytes += f.stat().st_size
            shutil.rmtree(pycache, ignore_errors=True)
        except Exception:
            pass

    # Archivos de log de backup (rotados): backend_*.log.1, .2, .3
    for f in BASE_DIR.glob(f"backend_{_INSTANCE_ID}.log.*"):
        try:
            freed_bytes += f.stat().st_size
            f.unlink(missing_ok=True)
        except Exception:
            pass

    # Archivos .tmp en el directorio backend
    for f in BASE_DIR.glob("*.tmp"):
        try:
            freed_bytes += f.stat().st_size
            f.unlink(missing_ok=True)
        except Exception:
            pass

    freed_mb = freed_bytes / (1024 * 1024)
    logger.info("Caché limpiada — %.2f MB liberados", freed_mb)
    return freed_mb


def _periodic_cleanup_loop():
    """
    Limpia caché local y snapshots viejas cada 6 horas mientras el backend
    está activo. Las fotos de Storage solo sirven para que la persona vea
    cómo quedó su registro EL MISMO DÍA — pasadas 24h se borran solas.
    """
    # Primera ejecución al arrancar (limpia restos de sesión anterior)
    cleanup_cache()
    try:
        _supabase.cleanup_old_snapshots(max_age_hours=24.0)
    except Exception:
        logger.exception("Error en limpieza inicial de snapshots")
    while not _shutdown.is_set():
        _shutdown.wait(timeout=6 * 3600)  # cada 6 horas
        if not _shutdown.is_set():
            cleanup_cache()
            try:
                _supabase.cleanup_old_snapshots(max_age_hours=24.0)
            except Exception:
                logger.exception("Error en limpieza periódica de snapshots")


# --------------------------------------------------------------------------
# Lógica anti-spam: decidir si capturar o no
# --------------------------------------------------------------------------

def should_capture(match, has_face: bool) -> bool:
    """
    Decide si se debe capturar una foto ahora mismo.
    Reglas:
      1. Debe haber un rostro presente.
      2. Si la misma persona sigue en cuadro, esperar su cooldown: las
         personas YA AUTORIZADAS usan AUTHORIZED_CAPTURE_COOLDOWN_SECONDS
         (más largo, p.ej. 30s) para no duplicar su registro de acceso si
         se quedan paradas frente a la cámara; el resto usa CAPTURE_COOLDOWN.
      3. Si es una persona nueva (o desconocida), capturar de inmediato.
      4. Si no hay rostro por más de FACE_LEAVE_TIMEOUT, resetear estado.
    """
    global _last_capture_time, _last_person_id, _last_person_time, _last_face_present_time

    now = time.time()

    if has_face:
        _last_face_present_time = now
    else:
        # Sin rostro: si pasó suficiente tiempo, resetear para允许 nueva captura
        if now - _last_face_present_time > FACE_LEAVE_TIMEOUT:
            _last_person_id = None
        return False

    # Determinar identidad actual
    current_id = match.person_id if match else None  # None = desconocido
    person_cooldown = (
        AUTHORIZED_CAPTURE_COOLDOWN_SECONDS
        if (match is not None and match.authorized)
        else CAPTURE_COOLDOWN_SECONDS
    )

    # ── Rate limiting por persona ───────────────────────────────────────
    # Si es la misma persona detectada antes, respetar su cooldown
    if current_id == _last_person_id and current_id is not None:
        if now - _last_person_time < person_cooldown:
            return False
    elif current_id is None and _last_person_id is None:
        # Desconocido tras desconocido: cooldown también
        if now - _last_person_time < CAPTURE_COOLDOWN_SECONDS:
            return False

    # ── Timing general ──────────────────────────────────────────────────
    if now - _last_capture_time < CAPTURE_COOLDOWN_SECONDS:
        return False

    # Actualizar estado
    _last_person_id = current_id
    _last_person_time = now
    _last_capture_time = now
    return True


def reset_rate_limit():
    """Resetea el rate limiter (útil al cambiar de cámara)."""
    global _last_capture_time, _last_person_id, _last_person_time, _last_face_present_time, _face_seen_since
    _last_capture_time = 0.0
    _last_person_id = None
    _last_person_time = 0.0
    _face_seen_since = None
    with _state_lock:
        _state["recognition"] = _idle_recognition_state()
    _last_face_present_time = 0.0


# --------------------------------------------------------------------------
# Bucle principal de captura
# --------------------------------------------------------------------------

def _free_port(port: int):
    """Mata cualquier proceso que esté usando el puerto dado (Windows)."""
    import subprocess
    try:
        # netstat para encontrar el PID del proceso usando el puerto
        # Filtro preciso: solo líneas LISTENING con el puerto exacto
        result = subprocess.run(
            f'netstat -ano | findstr "LISTENING" | findstr ":{port} "',
            shell=True, capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5:
                pid = parts[-1]
                if pid == "0":
                    continue
                logger.warning("Puerto %d ocupado por PID %s. Liberando...", port, pid)
                subprocess.run(f'taskkill /F /PID {pid}', shell=True, timeout=5)
                time.sleep(0.5)
    except Exception as exc:
        logger.debug("Error liberando puerto %d: %s", port, exc)


def _sync_state_from_source(s: dict) -> None:
    """Sincroniza _state con el estado de SourceManager."""
    with _state_lock:
        _state["source_type"] = s.get("source_type", _state["source_type"])
        _state["source_url"] = s.get("source_url")
        _state["camera_index"] = (
            int(s["source_id"]) if s.get("source_id", "").lstrip("-").isdigit()
            else -1
        )
        _state["camera_name"] = (
            f"{s['source_type']} ({s['source_id']})"
            if s.get("source_type") == "droidcam"
            else s.get("source_id", _state["camera_name"])
        )
        _state["fps"] = s.get("fps", _state["fps"])
        if s.get("active"):
            _state["status"] = "running"
        elif s.get("error"):
            _state["status"] = "error"


def _try_open_camera() -> bool:
    """
    Intenta abrir una fuente de video: USB → DroidCam (auto-discover).
    Usa SourceManager internamente, que prueba backends y escanea red.
    """
    config = SourceConfig(
        source_type="usb",
        usb_index=DEFAULT_CAMERA_INDEX,
        auto_discover=True,
    )
    success = _source.update_config(config)
    if success:
        s = _source.status()
        _sync_state_from_source(s)
        logger.info("Fuente activa: %s (%s)", s["source_type"], s["source_id"])
        return True

    logger.warning("No se encontró ninguna fuente de video")
    return False


def _run_capture_wrapper():
    """Wrapper que ejecuta capture_loop() en un thread y atrapa excepciones."""
    try:
        capture_loop()
    except Exception as exc:
        logger.exception("Capture loop finalizó con excepción: %s", exc)
    logger.info("Capture loop thread finalizado")


# ═══════════════════════════════════════════════════════════════════════
# HILO LECTOR DE CÁMARA (separado del bucle de detección)
# ═══════════════════════════════════════════════════════════════════════

def _camera_reader():
    """
    Hilo dedicado exclusivamente a leer frames de la cámara en bucle.
    - Mantiene _latest_frame actualizado para /api/snapshot (vía SourceManager)
    - Pone frames en _frame_buffer para que capture_loop() los consuma
    - Actualiza _last_frame_time para detección de caídas de cámara
    """
    global _last_frame_time
    logger.info("Hilo lector de cámara iniciado")

    while _capture_running.is_set():
        ok, frame = _source.get_frame()
        if ok and frame is not None:
            _last_frame_time = time.time()
            # Encode JPEG una sola vez aquí; /api/snapshot lo lee sin encode
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, SNAPSHOT_QUALITY])
            if ret:
                with _jpeg_lock:
                    _jpeg_shared["frame"] = buf.tobytes()
            # Actualizar buffer: descartar frame viejo si está lleno
            try:
                _frame_buffer.put_nowait(frame)
            except queue.Full:
                try:
                    _frame_buffer.get_nowait()
                    _frame_buffer.put_nowait(frame)
                except queue.Empty:
                    pass
        else:
            time.sleep(0.005)

    logger.info("Hilo lector de cámara finalizado")


def _process_detection_async(record: AccessRecord, snap_bytes: bytes | None,
                              match, person_id: str):
    """
    Procesa una detección de una persona REGISTRADA (insert + upload +
    attendance) en un hilo separado para no bloquear el bucle de captura.
    Solo se invoca cuando hubo match (nunca para "Desconocido").
    """
    def _worker():
        try:
            # Subir snapshot a Storage
            snap_url = None
            if snap_bytes:
                try:
                    snap_url = _supabase.upload_snapshot(snap_bytes)
                except Exception as upload_exc:
                    logger.warning("Snapshot no subida (continuando): %s", upload_exc)

            # Insertar access record (con fallback offline)
            ok = _supabase.insert_access_record(record)
            if not ok:
                _offline_q.enqueue("access_record", record.to_dict())
                with _state_lock:
                    _state["offline_pending"] = _offline_q.pending_count()
            _device_mgr.increment_detections()

            # Manejar attendance (entrada/salida)
            handle_attendance(_supabase, record.id, person_id)

            # Actualizar estado global con la URL del snapshot
            with _state_lock:
                _state["last_snapshot_url"] = snap_url
                _state["total_detections"] += 1
                if snap_url:
                    _state["last_detection"] = {
                        "person_id": person_id,
                        "full_name": match.full_name,
                        "confidence": match.confidence,
                        "similarity": match.similarity,
                        "result": record.result,
                        "snapshot_url": snap_url,
                    }
                    _state["last_person_name"] = match.full_name

            logger.info(
                "DETECTADO: %s (sim=%.3f, conf=%.2f, resultado=%s, foto=%s)",
                match.full_name, match.similarity, match.confidence, record.result,
                "si" if snap_url else "no",
            )
        except Exception as exc:
            logger.exception("Error en procesamiento async de detección: %s", exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ═══════════════════════════════════════════════════════════════════════
# BUCLE PRINCIPAL DE CAPTURA (ahora lee del buffer, no de la cámara)
# ═══════════════════════════════════════════════════════════════════════

def capture_loop():
    """
    Bucle principal headless (sin ventana OpenCV).
    Lee frames del buffer (llenado por _camera_reader), detecta rostros
    y envía detecciones a hilos async para registro en Supabase.
    La UI (video en vivo) la sirve via HTTP (/api/snapshot) para Flutter.
    Si la cámara actual falla, intenta con la siguiente disponible.
    """
    global _face_seen_since

    logger.info("Iniciando bucle de captura headless...")
    reset_rate_limit()
    _face_seen_since = None

    _supabase.ensure_storage_bucket()

    # Intentar abrir cámara (prueba varias hasta encontrar una que funcione)
    if not _try_open_camera():
        with _state_lock:
            _state["status"] = "error"
            _state["last_person_name"] = "Error: No se pudo abrir ninguna cámara"
        logger.error("No se pudo abrir ninguna cámara")
        return

    _capture_running.set()

    # Iniciar hilo lector de cámara (actualiza _latest_frame + llena buffer)
    reader_thread = threading.Thread(target=_camera_reader, daemon=True)
    reader_thread.start()

    camera_failures = 0
    consecutive_reconnect_failures = 0
    max_reconnect_failures = 3  # Tras 3 fallos de reconexión, probar otra fuente
    frame_count = 0
    # Guardar config actual para reconexión
    last_config = _source._config

    with _state_lock:
        _state["status"] = "running"

    try:
        while _capture_running.is_set():
            # Leer del buffer (no bloquea aunque la detección tarde)
            try:
                frame = _frame_buffer.get(timeout=1.0)
                camera_failures = 0
                consecutive_reconnect_failures = 0
            except queue.Empty:
                camera_failures += 1
                time.sleep(0.1)
                if camera_failures >= 15:  # ~15s sin frames
                    logger.warning("Buffer vacío. Reintentando reconexión...")
                    _capture_running.clear()
                    time.sleep(0.05)  # Dar tiempo al reader thread a salir de get_frame()
                    _source.stop()
                    time.sleep(1)
                    _capture_running.set()
                    if _source.update_config(last_config):
                        camera_failures = 0
                        consecutive_reconnect_failures = 0
                        # Reiniciar reader thread
                        reader_thread = threading.Thread(target=_camera_reader, daemon=True)
                        reader_thread.start()
                        logger.info("Reconexión exitosa")
                    else:
                        consecutive_reconnect_failures += 1
                        logger.warning(
                            "No se pudo reconectar (intento %d/%d)",
                            consecutive_reconnect_failures, max_reconnect_failures,
                        )
                        if consecutive_reconnect_failures >= max_reconnect_failures:
                            logger.info("Buscando otra fuente...")
                            _capture_running.clear()
                            if _try_open_camera():
                                camera_failures = 0
                                consecutive_reconnect_failures = 0
                                last_config = _source._config
                                _capture_running.set()
                                reader_thread = threading.Thread(target=_camera_reader, daemon=True)
                                reader_thread.start()
                                s = _source.status()
                                logger.info(
                                    "Cambiado a %s (%s)",
                                    s["source_type"], s["source_id"],
                                )
                            else:
                                logger.warning("No hay fuentes disponibles")
                continue

            frame_count += 1

            # FPS desde SourceManager (lector thread actualiza _latest_frame)
            s = _source.status()
            with _state_lock:
                _state["fps"] = s.get("fps", 0)

            # Skip frames (solo detectar cada N frames para rendimiento)
            if frame_count % DETECTION_FRAME_SKIP != 0:
                continue

            # Detectar rostros
            faces = _detector.detect_faces(frame)
            has_face = len(faces) > 0
            now = time.time()

            if not has_face:
                should_capture(None, has_face=False)
                # Si el rostro lleva ausente más de FACE_LEAVE_TIMEOUT, volver
                # la guía de pantalla a estado inactivo y reiniciar el contador.
                if _face_seen_since is not None and now - _last_face_present_time > FACE_LEAVE_TIMEOUT:
                    _face_seen_since = None
                    with _state_lock:
                        _state["recognition"] = _idle_recognition_state()
                continue

            # Si hay varios rostros, usar el de mayor confianza de detección
            # (más fiable que tomar siempre el primero del listado).
            face = max(faces, key=lambda f: f.get("confidence", 0.0))
            confidence = float(face.get("confidence", 0.0))
            embedding = face.get("embedding")

            match = None
            if embedding is not None:
                match = _detector.recognize(embedding)

            # ── Guía de reconocimiento EN VIVO ───────────────────────────
            # Se actualiza en cada frame analizado (independiente de si se
            # persiste o no) para que la UI muestre mensajes en tiempo real:
            # "Reconociendo...", calidad del match, y el aviso de "no
            # registrado" tras UNKNOWN_FACE_TIMEOUT_SECONDS sin éxito.
            if _face_seen_since is None:
                _face_seen_since = now
            elapsed = now - _face_seen_since

            if match and match.authorized:
                phase = "recognized"
            elif match:
                phase = "low_confidence"
            elif elapsed >= UNKNOWN_FACE_TIMEOUT_SECONDS:
                phase = "unknown"
            else:
                phase = "detecting"

            with _state_lock:
                _state["recognition"] = {
                    "has_face": True,
                    "phase": phase,
                    "person_name": match.full_name if match else None,
                    "similarity": match.similarity if match else 0.0,
                    "confidence": match.confidence if match else 0.0,
                    "quality": _quality_label(match.similarity if match else 0.0),
                    "seconds_elapsed": round(elapsed, 1),
                    "max_wait_seconds": UNKNOWN_FACE_TIMEOUT_SECONDS,
                }

            if confidence < MIN_CONFIDENCE_TO_CAPTURE:
                continue

            # Solo se registra entrada/salida de personas REGISTRADAS y
            # AUTORIZADAS (match confiable, sin ambigüedad): si no hay match
            # o el match no es lo bastante confiable, no se crea access_record,
            # no se sube snapshot ni se toca attendance — un "Desconocido" o
            # un match dudoso no aporta valor al historial de accesos, y la
            # guía en pantalla ya informa "no registrado" / "baja calidad".
            if match is None or not match.authorized:
                continue

            if not should_capture(match, has_face=True):
                continue

            # Capturar JPEG (llama a SourceManager que lee un frame fresco)
            snap = _source.capture_jpeg()
            if snap is None:
                continue

            person_id = match.person_id
            person_name = match.full_name
            conf = match.confidence
            sim = match.similarity
            embedding_id = match.matched_embedding_id
            result = "authorized"

            # Obtener source_id para el registro
            src_id = _source.status().get("source_id", str(DEFAULT_CAMERA_INDEX))
            src_type = _source.status().get("source_type", "usb")
            record = AccessRecord(
                company_id=COMPANY_ID,
                person_id=person_id,
                result=result,
                confidence=conf,
                similarity=sim,
                matched_face_embedding_id=embedding_id,
                source_name=f"{src_type}_{src_id}",
                camera_kind=src_type,
            )

            # Enviar a hilo async para no bloquear el bucle de captura
            _process_detection_async(record, snap, match, person_id)

    except KeyboardInterrupt:
        logger.info("Captura interrumpida por el usuario")
    except Exception as exc:
        logger.exception("Error en bucle de captura: %s", exc)
        with _state_lock:
            _state["status"] = "error"
    finally:
        _capture_running.clear()
        _source.stop()
        reset_rate_limit()
        # Vaciar buffer
        while not _frame_buffer.empty():
            try:
                _frame_buffer.get_nowait()
            except queue.Empty:
                break
        with _state_lock:
            _state["status"] = "paused"
        logger.info("Bucle de captura finalizado")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _watchdog():
    """
    Watchdog que monitorea el bucle de captura y lo reinicia si se detiene.
    Corre en el hilo principal. Flask y capture_loop() corren en daemon threads.
    """
    capture_thread: threading.Thread | None = None

    while not _shutdown.is_set():
        # Si el thread de captura no está vivo, reiniciarlo
        if capture_thread is None or not capture_thread.is_alive():
            if capture_thread is not None:
                logger.warning(
                    "Watchdog: bucle de captura detenido. Reiniciando..."
                )
            else:
                logger.info("Watchdog: iniciando bucle de captura...")

            _source.close()
            _capture_running.clear()
            reset_rate_limit()

            capture_thread = threading.Thread(
                target=_run_capture_wrapper,
                daemon=True,
            )
            capture_thread.start()

        # Esperar 5 segundos o hasta que nos pidan shutdown
        try:
            _shutdown.wait(timeout=5.0)
        except KeyboardInterrupt:
            logger.info("SIGINT recibido. Apagando...")
            _shutdown.set()
            break


def _cleanup_files():
    """Limpia todos los archivos residuales del backend al cerrar."""
    base = BASE_DIR

    # PID file
    try:
        (base / f"backend_{_INSTANCE_ID}.pid").unlink(missing_ok=True)
        logger.debug("PID file eliminado")
    except Exception:
        pass

    # Token file
    try:
        (base / f"backend_{_INSTANCE_ID}.token").unlink(missing_ok=True)
        logger.debug("Token file eliminado")
    except Exception:
        pass

    # Log file
    try:
        (base / f"backend_{_INSTANCE_ID}.log").unlink(missing_ok=True)
        logger.debug("Log file eliminado")
    except Exception:
        pass

    # __pycache__
    pycache = base / "__pycache__"
    if pycache.exists() and pycache.is_dir():
        try:
            shutil.rmtree(pycache, ignore_errors=True)
        except Exception:
            pass

    # Archivos .spec de PyInstaller
    for f in base.glob("*.spec"):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass

    # build/ y dist/ de PyInstaller
    for d in ["build", "dist"]:
        p = base / d
        if p.exists() and p.is_dir():
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    logger.debug("Archivos residuales eliminados")


def main():
    logger.info("=" * 50)
    logger.info("BioFace - Sistema de Reconocimiento Facial (Headless)")
    logger.info("=" * 50)

    # Actualizar device_uid en estado global (ya fue calculado al inicio del módulo)
    with _state_lock:
        _state["device_uid"] = _DEVICE_UID

    # Logging remoto a Supabase (WARNING+ en background)
    attach_remote_logging(_supabase)

    # Verificaciones de arranque: licencia + OTA
    run_startup_checks()

    # Registrar dispositivo en Supabase + iniciar heartbeat
    _device_mgr.register()
    _device_mgr.start_heartbeat()

    # Cola offline: hilo de sincronización cada 10 min
    _offline_q.start_sync_daemon(_supabase)

    # Hilo de limpieza periódica de caché (cada 6 horas)
    threading.Thread(target=_periodic_cleanup_loop, daemon=True,
                     name="cache-cleanup").start()

    # Hilo de check de conexión a Supabase (actualiza _state["supabase_online"])
    def _connectivity_loop():
        while not _shutdown.is_set():
            online = _supabase.check_connection()
            with _state_lock:
                _state["supabase_online"] = online
                _state["offline_pending"] = _offline_q.pending_count()
            # Si volvió la conexión, intentar sync inmediato
            if online and _offline_q.pending_count() > 0:
                _offline_q.try_sync_now()
            _shutdown.wait(timeout=30)

    threading.Thread(target=_connectivity_loop, daemon=True,
                     name="connectivity-check").start()

    # Liberar puerto si está ocupado (por un cierre previo abrupto)
    _free_port(FLASK_PORT)

    # Escribir PID file para que Flutter pueda matarnos si es necesario
    pid_file = Path(BASE_DIR / f"backend_{_INSTANCE_ID}.pid")
    try:
        pid_file.write_text(str(os.getpid()))
    except Exception as exc:
        logger.debug("No se pudo escribir PID file: %s", exc)

    # Escribir token para que Flutter pueda autenticar sus llamadas
    try:
        _TOKEN_FILE.write_text(_BACKEND_TOKEN)
        logger.info("Token de autenticación escrito en backend.token")
    except Exception as exc:
        logger.warning("No se pudo escribir token file: %s", exc)

    # Servicio de enrollment HTTP (reutiliza el modelo del FaceDetector)
    _enroll_svc = EnrollService(_supabase, face_app=getattr(_detector, "_app", None))

    # Registrar rutas (necesita globals ya inicializados)
    register_routes(app, {
        "state": _state,
        "state_lock": _state_lock,
        "source": _source,
        "detector": _detector,
        "shutdown": _shutdown,
        "jpeg_lock": _jpeg_lock,
        "jpeg_shared": _jpeg_shared,       # dict mutable compartido {"frame": bytes|None}
        "reset_rate_limit": reset_rate_limit,
        "sync_state": _sync_state_from_source,
        "try_open_camera": _try_open_camera,
        "backend_token": _BACKEND_TOKEN,
        "public_endpoints": _PUBLIC_ENDPOINTS,
        "base_dir": BASE_DIR,
        "enroll_service": _enroll_svc,
        "supabase": _supabase,
        "offline_queue": _offline_q,
        "cleanup_cache_fn": cleanup_cache,
    })

    flask_thread = threading.Thread(
        target=lambda: app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
    )
    flask_thread.start()
    logger.info("HTTP API en http://%s:%d", FLASK_HOST, FLASK_PORT)

    try:
        _watchdog()
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown.set()
        _capture_running.clear()
        _source.close()
        _device_mgr.stop()
        _offline_q.stop()
        _cleanup_files()
        logger.info("Sistema detenido — todo limpio")


if __name__ == "__main__":
    main()
