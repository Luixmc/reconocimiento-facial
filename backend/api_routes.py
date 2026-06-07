"""
Rutas Flask del backend. Registradas vía register_routes() para evitar
imports circulares con los globals de main.py.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from flask import Flask, Response, jsonify, request, send_file

from source_manager import SourceConfig

logger = logging.getLogger(__name__)


def register_routes(app: Flask, deps: dict[str, Any]) -> None:
    """
    Registra todas las rutas en `app`.

    deps esperados:
      state          dict mutable con el estado global
      state_lock     threading.Lock para _state
      source         SourceManager
      detector       FaceDetector
      shutdown       threading.Event  (señal de apagado)
      jpeg_lock      threading.Lock
      jpeg_cache_ref lista de 1 elemento [bytes|None] — mutable container
      reset_rate_limit  callable()
      sync_state     callable(source_status_dict)
      try_open_camera   callable() -> bool
      backend_token  str
      public_endpoints set[str]
      base_dir       Path
    """
    _state       = deps["state"]
    _state_lock  = deps["state_lock"]
    _source      = deps["source"]
    _detector    = deps["detector"]
    _shutdown    = deps["shutdown"]
    _jpeg_lock   = deps["jpeg_lock"]
    _jpeg_shared = deps["jpeg_shared"]         # {"frame": bytes | None}
    _reset_rl    = deps["reset_rate_limit"]
    _sync_state  = deps["sync_state"]
    _try_cam     = deps["try_open_camera"]
    _token       = deps["backend_token"]
    _public      = deps["public_endpoints"]
    _offline_q   = deps.get("offline_queue")
    _base_dir    = deps.get("base_dir")
    _cleanup_fn  = deps.get("cleanup_cache_fn")

    # ── Auth middleware ───────────────────────────────────────────────────

    @app.before_request
    def _require_token():
        import secrets as _secrets
        if request.path in _public:
            return None
        tok = request.headers.get("X-Backend-Token", "")
        if not _secrets.compare_digest(tok, _token):
            return jsonify({"error": "Unauthorized"}), 401

    # ── Health & status ───────────────────────────────────────────────────

    @app.route("/api/health")
    def api_health():
        return jsonify({"ok": True, "service": "face-recognition-backend"})

    @app.route("/api/status")
    def api_status():
        with _state_lock:
            return jsonify({**json.loads(json.dumps(_state, default=str))})

    # ── Snapshot (stream de video para Flutter) ───────────────────────────

    @app.route("/api/snapshot")
    def api_snapshot():
        with _jpeg_lock:
            jpeg = _jpeg_shared["frame"]
        if jpeg is None:
            return Response("No frame", status=503)
        return Response(jpeg, mimetype="image/jpeg")

    # ── Embeddings ────────────────────────────────────────────────────────

    @app.route("/api/refresh-embeddings", methods=["POST"])
    def api_refresh_embeddings():
        _detector.refresh_embeddings()
        return jsonify({"ok": True})

    # ── Shutdown ──────────────────────────────────────────────────────────

    @app.route("/api/shutdown", methods=["POST"])
    def api_shutdown():
        logger.info("Shutdown solicitado vía API")

        def _delayed():
            time.sleep(0.3)
            _shutdown.set()
            time.sleep(2.0)
            os._exit(0)

        threading.Thread(target=_delayed, daemon=True).start()
        return jsonify({"ok": True, "message": "Apagando backend..."})

    # ── Cámara (legacy compat) ────────────────────────────────────────────

    @app.route("/api/cameras")
    def api_cameras():
        return jsonify(_source.list_sources())

    @app.route("/api/camera/test")
    def api_camera_test():
        s = _source.status()
        result = {
            "ok": False,
            "active": s["active"],
            "source_type": s["source_type"],
            "source_id": s["source_id"],
            "frame": False, "jpeg": False,
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

        h, w = frame.shape[:2]
        result["frame"] = True
        result["dimensions"] = f"{w}x{h}"
        jpeg = _source.capture_jpeg()
        if jpeg is not None:
            result["jpeg"] = True
            result["jpeg_size"] = len(jpeg)
        result["ok"] = True
        return jsonify(result)

    @app.route("/api/camera/select", methods=["POST"])
    def api_camera_select():
        from config import DEFAULT_CAMERA_INDEX
        data = request.get_json(force=True, silent=True) or {}
        index = data.get("index")
        src = data.get("source", "usb")
        url = data.get("url")

        config = SourceConfig(
            source_type="droidcam" if (src == "droidcam" or index == -1) else "usb",
            manual_url=url,
            usb_index=int(index) if index is not None and index != -1 else DEFAULT_CAMERA_INDEX,
            auto_discover=True,
        )
        _reset_rl()
        if _source.update_config(config):
            s = _source.status()
            _sync_state(s)
            return jsonify({"ok": True, "source_type": s["source_type"],
                            "source_id": s["source_id"], "source_url": s["source_url"]})
        if not _try_cam():
            return jsonify({"ok": False, "error": "No se pudo abrir ninguna fuente"}), 400
        s = _source.status()
        _sync_state(s)
        return jsonify({"ok": False, "error": "No se pudo abrir la fuente solicitada"}), 400

    # ── Source unificado ──────────────────────────────────────────────────

    @app.route("/api/source/status")
    def api_source_status():
        return jsonify(_source.status())

    @app.route("/api/source/list")
    def api_source_list():
        return jsonify(_source.list_sources())

    @app.route("/api/source/select", methods=["POST"])
    def api_source_select():
        data = request.get_json(force=True, silent=True) or {}
        config = SourceConfig(
            source_type=data.get("source_type", "usb"),
            manual_url=data.get("manual_url"),
            usb_index=data.get("usb_index", 0),
            auto_discover=data.get("auto_discover", True),
        )
        _reset_rl()
        if _source.update_config(config):
            s = _source.status()
            _sync_state(s)
            return jsonify({"ok": True, "source_type": s["source_type"],
                            "source_id": s["source_id"], "source_url": s["source_url"]})
        if _try_cam():
            s = _source.status()
            _sync_state(s)
            return jsonify({"ok": False,
                            "error": f"Fuente {config.source_type} no disponible",
                            "fallback": True,
                            "fallback_source_type": s["source_type"],
                            "fallback_source_id": s["source_id"]}), 200
        return jsonify({"ok": False, "error": "No se pudo abrir ninguna fuente"}), 400

    @app.route("/api/source/scan-droidcam", methods=["POST"])
    @app.route("/api/camera/scan-droidcam", methods=["POST"])
    def api_source_scan_droidcam():
        url = _source.scan_droidcam()
        if url:
            ip = url.split("/")[2].split(":")[0] if "/" in url else "?"
            return jsonify({"ok": True, "url": url, "ip": ip})
        return jsonify({"ok": False, "url": None,
                        "message": "No se encontró DroidCam en la red local"})

    # ── Enrollment remoto ─────────────────────────────────────────────────

    _enroll_service = deps.get("enroll_service")

    @app.route("/api/persons")
    def api_persons():
        """Lista personas registradas disponibles para enrollment."""
        from config import COMPANY_ID, SUPABASE_KEY, SUPABASE_URL
        import requests as _req
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/registered_persons"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        params = {
            "select": "id,full_name,document_number,position",
            "company_id": f"eq.{COMPANY_ID}",
            "status": "eq.active",
            "order": "full_name.asc",
        }
        try:
            resp = _req.get(url, headers=headers, params=params, timeout=8)
            if resp.ok:
                persons = resp.json()
                # Agregar conteo de embeddings por persona
                for p in persons:
                    p["embedding_count"] = deps["supabase"].count_embeddings(p["id"])
                return jsonify(persons)
        except Exception as exc:
            logger.warning("Error listando personas: %s", exc)
        return jsonify([]), 503

    # ── Admin panel endpoints ─────────────────────────────────────────────

    _supabase_client = deps.get("supabase")

    @app.route("/api/admin/access-records")
    def api_admin_access_records():
        """Últimos N access_records de la empresa."""
        limit = min(int(request.args.get("limit", 50)), 200)
        data = _supabase_client.fetch_recent_access_records(limit=limit) if _supabase_client else []
        return jsonify(data)

    @app.route("/api/admin/attendances")
    def api_admin_attendances():
        """Asistencias de hoy con nombre de persona."""
        data = _supabase_client.fetch_today_attendances() if _supabase_client else []
        return jsonify(data)

    @app.route("/api/admin/logs")
    def api_admin_logs():
        """Últimos N logs remotos del backend."""
        limit = min(int(request.args.get("limit", 100)), 500)
        data = _supabase_client.fetch_recent_logs(limit=limit) if _supabase_client else []
        return jsonify(data)

    @app.route("/api/enroll", methods=["POST"])
    def api_enroll():
        """
        Enrollment facial desde Flutter.
        Multipart: person_id (campo) + image (archivo JPEG).
        """
        if _enroll_service is None:
            return jsonify({"ok": False, "error": "Servicio de enrollment no disponible"}), 503

        person_id = request.form.get("person_id", "").strip()
        if not person_id:
            return jsonify({"ok": False, "error": "person_id requerido"}), 400

        file = request.files.get("image")
        if file is None:
            return jsonify({"ok": False, "error": "imagen requerida (campo 'image')"}), 400

        image_bytes = file.read()
        if not image_bytes:
            return jsonify({"ok": False, "error": "imagen vacía"}), 400

        result = _enroll_service.enroll_from_bytes(person_id, image_bytes)

        # Si el enrollment fue exitoso, recargar embeddings en el detector
        if result.get("ok"):
            _detector.refresh_embeddings()

        status_code = 200 if result.get("ok") else 422
        return jsonify(result), status_code

    # ── Exportar base de datos offline ────────────────────────────────────

    @app.route("/api/export-offline-db")
    def api_export_offline_db():
        """Descarga el archivo SQLite de la cola offline como adjunto."""
        if _offline_q is None:
            return jsonify({"error": "Cola offline no disponible"}), 503
        import pathlib
        db_path = pathlib.Path(_offline_q.db_path())
        if not db_path.exists():
            # Base de datos vacía — devolver un SQLite mínimo vacío sería raro;
            # mejor indicar que no hay datos pendientes
            return jsonify({"error": "No hay base de datos offline (sin registros pendientes)"}), 404
        return send_file(
            str(db_path),
            as_attachment=True,
            download_name="offline_records.db",
            mimetype="application/octet-stream",
        )

    # ── Limpiar caché ─────────────────────────────────────────────────────

    @app.route("/api/clear-cache", methods=["POST"])
    def api_clear_cache():
        """Limpia archivos temporales: __pycache__, logs de backup, .tmp."""
        if _cleanup_fn is None:
            return jsonify({"error": "Función de limpieza no disponible"}), 503
        freed_mb = _cleanup_fn()
        return jsonify({"ok": True, "freed_mb": round(freed_mb, 2)})
