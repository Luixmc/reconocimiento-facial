"""
Gestión del ID permanente del dispositivo (PC/terminal biométrica).
- Genera un UID único y persistente en el primer arranque
- Lo registra en Supabase devices vía upsert
- Mantiene heartbeat (is_online + last_seen_at + detections_today)
- Descarga configuración remota (camera_configs + system_settings)
"""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase_client import SupabaseClient

logger = logging.getLogger("device_manager")

_UID_FILE = Path(__file__).parent / "device_id.txt"
_APP_VERSION = "1.0.0"


def get_or_create_device_uid() -> str:
    """
    Retorna el UID permanente de este dispositivo.
    En el primer arranque lo genera a partir del hardware y lo persiste.
    Formato: BF-XXXXXXXXXXXXXXXX (16 hex chars uppercase)
    """
    if _UID_FILE.exists():
        try:
            uid = _UID_FILE.read_text().strip()
            if uid.startswith("BF-") and len(uid) >= 8:
                return uid
        except Exception:
            pass

    # Generar UID determinístico con info del equipo
    parts = [
        platform.node(),
        platform.machine(),
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
    ]
    raw = "|".join(p for p in parts if p)
    uid = "BF-" + hashlib.sha256(raw.encode()).hexdigest()[:16].upper()

    try:
        _UID_FILE.write_text(uid, encoding="utf-8")
        logger.info("Device UID generado: %s", uid)
    except Exception as exc:
        logger.warning("No se pudo persistir device UID: %s", exc)

    return uid


class DeviceManager:
    """
    Ciclo de vida del dispositivo: registro, heartbeat y config remota.
    """

    HEARTBEAT_INTERVAL = 60  # segundos entre heartbeats

    def __init__(
        self,
        supabase: SupabaseClient,
        device_uid: str,
        company_id: str,
        app_version: str = _APP_VERSION,
    ) -> None:
        self._supa = supabase
        self._uid = device_uid
        self._company_id = company_id
        self._version = app_version

        self._lock = threading.Lock()
        self._detections_today: int = 0
        self._camera_configs: list[dict] = []
        self._remote_settings: dict[str, Any] = {}

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def device_uid(self) -> str:
        return self._uid

    @property
    def camera_configs(self) -> list[dict]:
        with self._lock:
            return list(self._camera_configs)

    @property
    def remote_settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._remote_settings)

    def get_camera_role(self, camera_index: int) -> str:
        """
        Retorna el rol de la cámara dada su índice.
        Valores: 'entrada' | 'salida' | 'ambas'
        Por defecto 'ambas' si no está configurado.
        """
        with self._lock:
            for cfg in self._camera_configs:
                if cfg.get("camera_index") == camera_index:
                    return cfg.get("role", "ambas")
        return "ambas"

    def increment_detections(self) -> None:
        with self._lock:
            self._detections_today += 1

    # ── Registro ─────────────────────────────────────────────────────────────

    def register(self) -> bool:
        """Registra (o actualiza) el dispositivo en Supabase al arrancar."""
        try:
            device_name = f"Terminal {platform.node()}"
            ok = self._supa.upsert_device(
                device_uid=self._uid,
                company_id=self._company_id,
                name=device_name,
                app_version=self._version,
            )
            if ok:
                logger.info("Dispositivo registrado en Supabase: %s", self._uid)
                self._fetch_remote_config()
            return ok
        except Exception as exc:
            logger.warning("No se pudo registrar dispositivo: %s", exc)
            return False

    # ── Heartbeat ────────────────────────────────────────────────────────────

    def start_heartbeat(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="device-heartbeat",
        )
        self._thread.start()
        logger.info("Heartbeat iniciado (cada %ds)", self.HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._supa.device_offline(self._uid)
        except Exception:
            pass

    # ── Internos ─────────────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(timeout=self.HEARTBEAT_INTERVAL):
            try:
                with self._lock:
                    det = self._detections_today
                self._supa.heartbeat_device(
                    device_uid=self._uid,
                    detections_today=det,
                    app_version=self._version,
                )
                self._fetch_remote_config()
            except Exception as exc:
                logger.debug("Heartbeat error: %s", exc)

    def _fetch_remote_config(self) -> None:
        try:
            cfg = self._supa.get_device_config(self._uid, self._company_id)
            if cfg:
                with self._lock:
                    self._camera_configs = cfg.get("camera_configs", [])
                    self._remote_settings = cfg.get("system_settings", {})
                logger.debug(
                    "Config remota: %d cámaras, %d settings",
                    len(self._camera_configs),
                    len(self._remote_settings),
                )
        except Exception as exc:
            logger.debug("fetch_remote_config error: %s", exc)
