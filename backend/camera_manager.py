"""
Módulo de gestión de cámaras vía OpenCV compatible con Windows.
Permite listar, seleccionar y capturar frames de cámaras USB.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CameraManager:
    """Gestiona el ciclo de vida de una cámara vía OpenCV."""

    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None
        self._current_index: int = -1
        self._source_url: str | None = None  # URL para cámaras IP (DroidCam)
        self._lock = threading.Lock()
        self._running = False
        self._latest_frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._on_frame: Callable[[np.ndarray], None] | None = None

    # ── Descubrimiento ──────────────────────────────────────────────────

    @staticmethod
    def list_cameras(max_index: int = 30) -> list[dict]:
        """
        Lista las cámaras REALES del sistema usando DirectShow (pygrabber).
        NO itera índices al azar — solo muestra los dispositivos que Windows
        reconoce como cámaras de video.
        
        Para cada cámara real detectada, intenta abrirla con CAP_ANY para
        obtener resolución y verificar que entrega frames.
        """
        import time

        # Obtener nombres reales de dispositivos vía DirectShow —
        # esto es la fuente de verdad de qué cámaras existen
        device_names = CameraManager._get_device_names()
        if not device_names:
            logger.warning(
                "No se pudieron obtener dispositivos reales vía DirectShow. "
                "Lista vacía."
            )
            return []

        logger.info(
            "Dispositivos de video reales: %s",
            list(device_names.values()),
        )

        cameras: list[dict] = []

        # Solo iterar sobre los índices que pygrabber reporta como reales
        for idx in sorted(device_names.keys()):
            real_name = device_names[idx]
            try:
                cap = cv2.VideoCapture(idx, cv2.CAP_ANY)
                if cap.isOpened():
                    # Reintentar read() varias veces
                    frame_ok = False
                    for attempt in range(5):
                        ret, frame = cap.read()
                        if ret and frame is not None and frame.size > 0:
                            frame_ok = True
                            break
                        time.sleep(0.05)

                    if frame_ok:
                        # Obtener resolución
                        try:
                            w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                            h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                            res = f" [{int(w)}x{int(h)}]" if w > 0 and h > 0 else ""
                        except Exception:
                            res = ""

                        cameras.append({
                            "index": idx,
                            "name": f"{real_name}{res}",
                            "backend": "Auto",
                        })
                    else:
                        # El dispositivo existe pero no da frame aún
                        cameras.append({
                            "index": idx,
                            "name": f"{real_name} — sin señal",
                            "backend": "Auto",
                            "no_signal": True,
                        })
                    cap.release()
                else:
                    # El dispositivo existe en DirectShow pero OpenCV no pudo abrirlo
                    cameras.append({
                        "index": idx,
                        "name": f"{real_name} — no accesible",
                        "backend": "Auto",
                        "no_signal": True,
                    })
            except Exception as exc:
                logger.debug("Error al probar cámara real %d (%s): %s", idx, real_name, exc)
                cameras.append({
                    "index": idx,
                    "name": f"{real_name} — error",
                    "backend": "Auto",
                    "no_signal": True,
                })

        return cameras

    @staticmethod
    def _get_device_names() -> dict[int, str]:
        """
        Obtiene los nombres REALES de los dispositivos de video
        usando DirectShow (pygrabber).
        Retorna un dict {indice: nombre_real}.
        Los índices coinciden con los de OpenCV con backend DSHOW.
        """
        try:
            from pygrabber.dshow_graph import FilterGraph
            graph = FilterGraph()
            devices = graph.get_input_devices()
            result = {}
            for i, name in enumerate(devices):
                if name and name.strip():
                    result[i] = name.strip()
            return result
        except Exception:
            return {}

    @staticmethod
    def _get_camera_name(
        cap: cv2.VideoCapture,
        idx: int,
        backend_name: str,
        device_names: dict[int, str] | None = None,
    ) -> str:
        """
        Obtiene un nombre descriptivo para la cámara.
        Si hay un nombre real del dispositivo (vía pygrabber/DirectShow),
        lo usa. Si no, genera un nombre con índice + backend + resolución.
        """
        # Si tenemos un nombre real del dispositivo, usarlo
        if device_names and idx in device_names:
            real_name = device_names[idx]
            # Agregar resolución si está disponible
            try:
                w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                if w > 0 and h > 0:
                    return f"{real_name} [{int(w)}x{int(h)}]"
            except Exception:
                pass
            return real_name

        # Fallback: nombre genérico
        try:
            w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            fmt = f"{int(w)}x{int(h)}" if w > 0 and h > 0 else ""
        except Exception:
            fmt = ""

        parts = [f"Cámara {idx}"]
        if backend_name == "DirectShow":
            parts.append("(DSHOW)")
        elif backend_name == "MediaFoundation":
            parts.append("(MSMF)")
        if fmt:
            parts.append(f"[{fmt}]")
        return " ".join(parts)

    # ── Apertura / cierre ───────────────────────────────────────────────

    def open(self, source: int | str = 0) -> bool:
        """Abre una cámara por índice USB o por URL de red (DroidCam).

        - Si `source` es un entero: prueba backends en orden (CAP_ANY → DSHOW → MSMF).
        - Si `source` es un string (URL): lo pasa directamente a OpenCV (usa FFMPEG).

        Retorna True si la cámara se abrió correctamente.
        """
        import platform
        is_windows = platform.system() == "Windows"

        # Backends USB a probar en orden
        backends = [(cv2.CAP_ANY, "Auto")]
        if is_windows:
            backends += [
                (cv2.CAP_DSHOW, "DirectShow"),
                (cv2.CAP_MSMF, "MediaFoundation"),
            ]

        with self._lock:
            self.close()

            # ── Abrir por URL (DroidCam / cámara IP) ──────────────────
            if isinstance(source, str):
                try:
                    cap = cv2.VideoCapture(source)
                    if cap.isOpened():
                        self._cap = cap
                        self._current_index = -1
                        self._source_url = source
                        self._running = True
                        logger.info("Cámara IP abierta: %s", source)
                        return True
                    cap.release()
                except Exception as exc:
                    logger.debug("Error abriendo cámara IP %s: %s", source, exc)
                logger.warning("No se pudo abrir la cámara IP: %s", source)
                return False

            # ── Abrir por índice (USB / integrada) ────────────────────
            index = int(source)
            for backend_id, backend_name in backends:
                try:
                    cap = cv2.VideoCapture(index, backend_id)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    if cap.isOpened():
                        self._cap = cap
                        self._current_index = index
                        self._source_url = None
                        self._running = True
                        logger.info(
                            "Cámara %d abierta con %s", index, backend_name
                        )
                        return True
                    cap.release()
                except Exception as exc:
                    logger.debug(
                        "Backend %s falló para cámara %d: %s",
                        backend_name, index, exc,
                    )

            logger.warning(
                "No se pudo abrir la cámara %d con ningún backend", index
            )
            return False

    def close(self) -> None:
        """Libera la cámara."""
        self._running = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._current_index = -1
        self._source_url = None
        logger.info("Cámara liberada")

    @property
    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def source_url(self) -> str | None:
        """URL de la cámara IP (DroidCam), o None si es USB."""
        return self._source_url

    @property
    def is_droidcam(self) -> bool:
        """True si la cámara actual es DroidCam (IP)."""
        return self._source_url is not None

    # ── Captura ─────────────────────────────────────────────────────────

    def read_frame(self) -> np.ndarray | None:
        """Lee un frame de la cámara. Retorna None si falla.
        Captura excepciones C++ de OpenCV (cámara desconectada, etc.)
        para que el bucle principal pueda reconectar.
        """
        if not self.is_open or self._cap is None:
            return None
        try:
            ret, frame = self._cap.read()
            if ret:
                with self._frame_lock:
                    self._latest_frame = frame.copy()
                return frame
            return None
        except Exception as exc:
            logger.warning("Error leyendo frame de cámara %d: %s",
                           self._current_index, exc)
            return None

    def get_latest_frame(self) -> np.ndarray | None:
        """Retorna el último frame capturado (thread-safe).
        Protegido contra frames corruptos: si .copy() falla,
        limpia _latest_frame y retorna None.
        """
        with self._frame_lock:
            if self._latest_frame is not None:
                try:
                    return self._latest_frame.copy()
                except Exception as exc:
                    logger.warning("Frame corrupto en cámara %d, limpiando: %s",
                                   self._current_index, exc)
                    self._latest_frame = None
            return None

    def capture_jpeg(self, quality: int = 85) -> bytes | None:
        """Captura un frame y lo codifica como JPEG.
        Envuelto en try/except para evitar que excepciones C++
        de OpenCV (frame corrupto, etc.) crasheen el bucle.
        """
        try:
            frame = self.read_frame()
            if frame is None:
                return None
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ret:
                return buf.tobytes()
            return None
        except Exception as exc:
            logger.warning("Error codificando JPEG de cámara %d: %s",
                           self._current_index, exc)
            return None
