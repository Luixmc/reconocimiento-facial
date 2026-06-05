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
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import shutil
from flask import Flask, Response, jsonify, request

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from source_manager import SourceManager, SourceConfig
from config import (
    COMPANY_ID,
    DEFAULT_CAMERA_INDEX,
    DETECTION_FRAME_SKIP,
    FACE_LEAVE_TIMEOUT,
    CAPTURE_COOLDOWN_SECONDS,
    MIN_CONFIDENCE_TO_CAPTURE,
    FLASK_HOST,
    FLASK_PORT,
)
from face_detector import FaceDetector
from supabase_client import AccessRecord, SupabaseClient

# ── Logging con rotación ──────────────────────────────────────────────────
from logging.handlers import RotatingFileHandler

log_file = Path(BASE_DIR / "backend.log")
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
    "source_type": "usb",  # "usb" | "droidcam"
    "source_url": None,     # URL si es DroidCam
    "last_detection": None,
    "last_snapshot_url": None,
    "last_person_name": None,
    "fps": 0,
    "total_detections": 0,
}
_state_lock = threading.Lock()

_source = SourceManager()
_supabase = SupabaseClient()
_detector = FaceDetector(_supabase)

_capture_running = threading.Event()
_shutdown = threading.Event()  # Señal de shutdown para el watchdog

# ── Estado interno de rate-limiting ──────────────────────────────────────
_last_capture_time: float = 0.0
_last_person_id: str | None = None      # última persona detectada
_last_person_time: float = 0.0          # cuándo se detectó por última vez
_last_face_present_time: float = 0.0    # cuándo se vio un rostro por última vez

# ── Buffer de frames para hilo lector de cámara ─────────────────────────
_frame_buffer: queue.Queue = queue.Queue(maxsize=2)
_last_frame_time: float = 0.0


app = Flask(__name__)

# Silenciar logs HTTP de werkzeug (cada /api/snapshot genera un log por request)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# --------------------------------------------------------------------------
# API REST
# --------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    with _state_lock:
        return jsonify({**json.loads(json.dumps(_state, default=str))})


@app.route("/api/cameras")
def api_cameras():
    """Lista todas las fuentes disponibles (USB + DroidCam)."""
    return jsonify(_source.list_sources())


@app.route("/api/camera/test")
def api_camera_test():
    """Captura un frame de prueba y reporta si la fuente funciona."""
    s = _source.status()
    result = {
        "ok": False,
        "active": s["active"],
        "source_type": s["source_type"],
        "source_id": s["source_id"],
        "frame": False,
        "jpeg": False,
        "dimensions": None,
        "error": s.get("error"),
    }

    if not s["active"]:
        result["error"] = result["error"] or "Fuente no activa"
        return jsonify(result), 503

    ok, frame = _source.get_frame()
    if not ok or frame is None:
        result["error"] = "No se pudo leer frame"
        return jsonify(result), 503

    result["frame"] = True
    h, w = frame.shape[:2]
    result["dimensions"] = f"{w}x{h}"

    jpeg = _source.capture_jpeg()
    if jpeg is not None:
        result["jpeg"] = True
        result["jpeg_size"] = len(jpeg)

    result["ok"] = True
    return jsonify(result)


@app.route("/api/camera/select", methods=["POST"])
def api_camera_select():
    """Cambia la cámara activa (backward compat).
    
    Formatos aceptados (antiguo):
      {"index": 0}
      {"index": -1, "source": "droidcam"}
      {"source": "droidcam", "url": "..."}
    
    Internamente delega en /api/source/select.
    """
    data = request.get_json(force=True, silent=True) or {}
    index = data.get("index")
    src = data.get("source", "usb")
    url = data.get("url")

    # Convertir formato antiguo a SourceConfig
    if src == "droidcam" or index == -1:
        config = SourceConfig(
            source_type="droidcam",
            manual_url=url,
            auto_discover=True,
        )
    else:
        config = SourceConfig(
            source_type="usb",
            usb_index=int(index) if index is not None else DEFAULT_CAMERA_INDEX,
            auto_discover=False,
        )

    reset_rate_limit()
    success = _source.update_config(config)

    if success:
        s = _source.status()
        _sync_state_from_source(s)
        return jsonify({
            "ok": True,
            "source_type": s["source_type"],
            "source_id": s["source_id"],
            "source_url": s["source_url"],
        })

    # Restaurar fuente anterior con auto-discover
    logger.warning("No se pudo abrir fuente solicitada. Buscando otra...")
    if not _try_open_camera():
        return jsonify({"ok": False, "error": "No se pudo abrir ninguna fuente"}), 400

    s = _source.status()
    _sync_state_from_source(s)
    return jsonify({"ok": False, "error": "No se pudo abrir la fuente solicitada"}), 400


@app.route("/api/snapshot")
def api_snapshot():
    frame = _source.get_latest_frame()
    if frame is None:
        return Response("No frame", status=503)
    ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ret:
        return Response("Encode error", status=500)
    return Response(buf.tobytes(), mimetype="image/jpeg")


@app.route("/api/refresh-embeddings", methods=["POST"])
def api_refresh_embeddings():
    _detector.refresh_embeddings()
    return jsonify({"ok": True})


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "service": "face-recognition-backend"})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """
    Apaga el backend de forma controlada.
    
    Flujo:
      1. Responde 200 OK inmediatamente (el cliente recibe confirmación)
      2. Espera 300ms en hilo separado (tiempo para que la respuesta viaje)
      3. Setea _shutdown → el watchdog sale → finally corre limpiamente
         (libera cámara, borra PID file, cierra conexiones)
    """
    logger.info("Shutdown solicitado vía API — respondiendo antes de apagar")

    def _delayed_shutdown():
        time.sleep(0.3)  # 300ms: suficiente para que el 200 OK llegue al cliente
        logger.info("Ejecutando shutdown ordenado...")
        _shutdown.set()
        # Dar 2 segundos al watchdog para que salga limpiamente
        # antes de forzar la salida del proceso
        time.sleep(2.0)
        logger.info("Proceso terminando.")
        os._exit(0)  # Salida limpia que no levanta excepciones en hilos daemon

    t = threading.Thread(target=_delayed_shutdown, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Apagando backend..."})


# --------------------------------------------------------------------------
# API /api/source/* — Fuentes de video (unificadas)
# --------------------------------------------------------------------------

@app.route("/api/source/status")
def api_source_status():
    """Estado actual de la fuente de video."""
    return jsonify(_source.status())


@app.route("/api/source/list")
def api_source_list():
    """Lista todas las fuentes disponibles."""
    return jsonify(_source.list_sources())


@app.route("/api/source/select", methods=["POST"])
def api_source_select():
    """Selecciona una fuente de video.

    Body:
      {"source_type": "usb", "usb_index": 0}
      {"source_type": "droidcam", "manual_url": "http://...:4747/video"}
      {"source_type": "droidcam", "auto_discover": true}
      {"manual_url": "rtsp://192.168.1.50/stream1"}
      {"source_type": "manual", "manual_url": "http://..."}

    Retorna:
      {"ok": true, "source_type": "...", "source_id": "...", "source_url": "..."}
    """
    data = request.get_json(force=True, silent=True) or {}
    config = SourceConfig(
        source_type=data.get("source_type", "usb"),
        manual_url=data.get("manual_url"),
        usb_index=data.get("usb_index", 0),
        auto_discover=data.get("auto_discover", True),
    )

    reset_rate_limit()
    success = _source.update_config(config)

    if success:
        s = _source.status()
        _sync_state_from_source(s)
        return jsonify({
            "ok": True,
            "source_type": s["source_type"],
            "source_id": s["source_id"],
            "source_url": s["source_url"],
        })

    # Fallback: intentar con auto-discover
    logger.warning("Fuente %s no disponible. Buscando otra...", config.source_type)
    if _try_open_camera():
        s = _source.status()
        _sync_state_from_source(s)
        return jsonify({
            "ok": False,
            "error": f"Fuente {config.source_type} no disponible",
            "fallback": True,
            "fallback_source_type": s["source_type"],
            "fallback_source_id": s["source_id"],
        }), 200

    return jsonify({"ok": False, "error": "No se pudo abrir ninguna fuente"}), 400


@app.route("/api/source/scan-droidcam", methods=["POST"])
def api_source_scan_droidcam():
    """Escanea la red local en busca de DroidCam."""
    logger.info("Escaneando red en busca de DroidCam...")
    url = _source.scan_droidcam()
    if url:
        ip = url.split("/")[2].split(":")[0] if "/" in url else "?"
        logger.info("DroidCam encontrado: %s", url)
        return jsonify({"ok": True, "url": url, "ip": ip})
    return jsonify({"ok": False, "url": None,
                     "message": "No se encontró DroidCam en la red local"})


# (Legacy) Escanea red en busca de DroidCam
@app.route("/api/camera/scan-droidcam", methods=["POST"])
def api_camera_scan_droidcam():
    return api_source_scan_droidcam()


# --------------------------------------------------------------------------
# Lógica anti-spam: decidir si capturar o no
# --------------------------------------------------------------------------

def should_capture(match, has_face: bool) -> bool:
    """
    Decide si se debe capturar una foto ahora mismo.
    Reglas:
      1. Debe haber un rostro presente.
      2. Si la misma persona sigue en cuadro, esperar CAPTURE_COOLDOWN.
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

    # ── Rate limiting por persona ───────────────────────────────────────
    # Si es la misma persona detectada antes, respetar cooldown
    if current_id == _last_person_id and current_id is not None:
        if now - _last_person_time < CAPTURE_COOLDOWN_SECONDS:
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
    global _last_capture_time, _last_person_id, _last_person_time, _last_face_present_time
    _last_capture_time = 0.0
    _last_person_id = None
    _last_person_time = 0.0
    _last_face_present_time = 0.0


# --------------------------------------------------------------------------
# Lógica de attendance (entrada/salida)
# --------------------------------------------------------------------------

def handle_attendance(match, access_record_id: str, person_id: str | None):
    """
    Gestiona la apertura/cierre de attendances.
    - Si la persona NO tiene attendance abierta hoy → crea una (ENTRADA)
    - Si ya tiene una abierta → la cierra (SALIDA)
    """
    if not person_id:
        return

    existing = _supabase.get_today_attendance(person_id)

    if existing:
        # Ya tiene attendance abierta → marcar como SALIDA
        att_id = existing["id"]
        ok = _supabase.close_attendance(att_id)
        if ok:
            _supabase.insert_attendance_mark(att_id, access_record_id, "exit")
            logger.info("SALIDA registrada para persona %s", person_id)
    else:
        # No tiene → crear ENTRADA
        att = _supabase.create_attendance(person_id)
        if att:
            _supabase.insert_attendance_mark(att["id"], access_record_id, "entry")
            logger.info("ENTRADA registrada para persona %s", person_id)


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
                              match, person_id: str | None):
    """
    Procesa una detección (insert + upload + attendance) en un hilo separado
    para no bloquear el bucle de captura.
    """
    def _worker():
        try:
            # Subir snapshot a Storage
            snap_url = None
            if snap_bytes:
                snap_url = _supabase.upload_snapshot(snap_bytes)

            # Insertar access record
            _supabase.insert_access_record(record)

            # Manejar attendance (entrada/salida)
            handle_attendance(match, record.id, person_id)

            # Actualizar estado global con la URL del snapshot
            with _state_lock:
                _state["last_snapshot_url"] = snap_url
                _state["total_detections"] += 1
                if snap_url:
                    _state["last_detection"] = {
                        "person_id": person_id,
                        "full_name": getattr(match, 'full_name', 'Desconocido') if match else 'Desconocido',
                        "confidence": getattr(match, 'confidence', 0.0) if match else 0.0,
                        "similarity": getattr(match, 'similarity', 0.0) if match else 0.0,
                        "result": record.result,
                        "snapshot_url": snap_url,
                    }
                    _state["last_person_name"] = getattr(match, 'full_name', 'Desconocido') if match else 'Desconocido'

            person_name = getattr(match, 'full_name', 'Desconocido') if match else 'Desconocido'
            sim = getattr(match, 'similarity', 0.0) if match else 0.0
            conf = getattr(match, 'confidence', 0.0) if match else 0.0
            logger.info(
                "DETECTADO: %s (sim=%.3f, conf=%.2f, resultado=%s, foto=%s)",
                person_name, sim, conf, record.result,
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
    logger.info("Iniciando bucle de captura headless...")
    reset_rate_limit()

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

            if not has_face:
                should_capture(None, has_face=False)
                continue

            face = faces[0]
            confidence = float(face.get("confidence", 0.0))
            embedding = face.get("embedding")

            match = None
            if embedding is not None:
                match = _detector.recognize(embedding)

            if confidence < MIN_CONFIDENCE_TO_CAPTURE:
                continue
            if not should_capture(match, has_face=True):
                continue

            # Capturar JPEG (llama a SourceManager que lee un frame fresco)
            snap = _source.capture_jpeg()
            if snap is None:
                continue

            person_id = match.person_id if match else None
            person_name = match.full_name if match else "Desconocido"
            conf = match.confidence if match else 0.0
            sim = match.similarity if match else 0.0
            embedding_id = match.matched_embedding_id if match else None
            result = "authorized" if (match and match.authorized) else "not_found"

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
        (base / "backend.pid").unlink(missing_ok=True)
        logger.debug("PID file eliminado")
    except Exception:
        pass

    # Log file
    try:
        (base / "backend.log").unlink(missing_ok=True)
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

    # Liberar puerto si está ocupado (por un cierre previo abrupto)
    _free_port(FLASK_PORT)

    # Escribir PID file para que Flutter pueda matarnos si es necesario
    pid_file = Path(BASE_DIR / "backend.pid")
    try:
        pid_file.write_text(str(os.getpid()))
    except Exception as exc:
        logger.debug("No se pudo escribir PID file: %s", exc)

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
        _cleanup_files()
        logger.info("Sistema detenido — todo limpio")


if __name__ == "__main__":
    main()
