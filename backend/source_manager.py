"""
SourceManager — Módulo unificado de fuentes de video.
"""
from __future__ import annotations

import io
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    source_type: str = "usb"
    manual_url: str | None = None
    usb_index: int = 0
    auto_discover: bool = True


@dataclass
class SourceStatus:
    active: bool = False
    source_type: str = ""
    source_id: str = ""
    source_url: str | None = None
    fps: float = 0.0
    error: str | None = None
    resolution: str = ""


class MjpegReader:
    def __init__(self, url: str) -> None:
        self._url = url
        self._stream: io.BufferedIOBase | None = None
        self._buffer = b""
        self._opened = False
        self._open()

    def _open(self) -> None:
        try:
            req = urllib.request.Request(self._url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "*/*",
            })
            self._stream = urllib.request.urlopen(req, timeout=10)
            self._opened = True
        except Exception as exc:
            logger.debug("MjpegReader error conectando a %s: %s", self._url, exc)
            self._opened = False

    def isOpened(self) -> bool:
        return self._opened and self._stream is not None

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.isOpened():
            return False, None
        try:
            while True:
                chunk = self._stream.read(4096)
                if not chunk:
                    return False, None
                self._buffer += chunk
                start = self._buffer.find(b'\xff\xd8')
                if start == -1:
                    if len(self._buffer) > 2:
                        self._buffer = self._buffer[-2:]
                    continue
                end = self._buffer.find(b'\xff\xd9', start + 2)
                if end == -1:
                    continue
                jpeg_data = self._buffer[start:end + 2]
                self._buffer = self._buffer[end + 2:]
                frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
        except Exception as exc:
            logger.debug("MjpegReader.read error: %s", exc)
        return False, None

    def get(self, prop_id: int) -> float:
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        return False

    def release(self) -> None:
        self._opened = False
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._buffer = b""


DetectorFunc = Callable[["SourceManager", SourceConfig], cv2.VideoCapture | MjpegReader | None]


class SourceManager:
    _DETECTORS: dict[str, DetectorFunc] = {}
    STREAM_PATHS: list[str] = ["/video", "/mjpeg", "/"]

    def __init__(self) -> None:
        self._config = SourceConfig()
        self._cap: cv2.VideoCapture | MjpegReader | None = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._status = SourceStatus()
        self._droidcam_url: str | None = None
        self._frame_count = 0
        self._fps_timer = time.time()
        self._last_fps = 0.0

    def update_config(self, config: SourceConfig) -> bool:
        self._config = config
        logger.info("update_config: source_type=%s, manual=%s, auto=%s",
                    config.source_type, config.manual_url, config.auto_discover)
        with self._lock:
            self._close()
            cap = self._resolve(config)
            if cap is None:
                self._status = SourceStatus(
                    active=False,
                    error="No se pudo abrir ninguna fuente de video",
                )
                logger.warning("update_config: no se encontró ninguna fuente")
                return False
            self._cap = cap
            self._status.active = True
            self._status.error = None
            try:
                w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                if w > 0 and h > 0:
                    self._status.resolution = f"{int(w)}x{int(h)}"
            except Exception:
                pass
            logger.info("update_config: fuente activa — %s (%s)",
                        self._status.source_type, self._status.source_id)
            return True

    def get_frame(self) -> tuple[bool, np.ndarray | None]:
        if not self._status.active or self._cap is None:
            return False, None
        try:
            ret, frame = self._cap.read()
            if ret:
                with self._frame_lock:
                    self._latest_frame = frame.copy()
                self._frame_count += 1
                elapsed = time.time() - self._fps_timer
                if elapsed >= 1.0:
                    self._last_fps = round(self._frame_count / elapsed, 1)
                    self._status.fps = self._last_fps
                    self._frame_count = 0
                    self._fps_timer = time.time()
                return True, frame
            return False, None
        except Exception as exc:
            logger.warning("get_frame error: %s", exc)
            return False, None

    def get_latest_frame(self) -> np.ndarray | None:
        with self._frame_lock:
            if self._latest_frame is not None:
                try:
                    return self._latest_frame.copy()
                except Exception:
                    self._latest_frame = None
            return None

    def capture_jpeg(self, quality: int = 85) -> bytes | None:
        ok, frame = self.get_frame()
        if not ok or frame is None:
            return None
        try:
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ret:
                return buf.tobytes()
        except Exception as exc:
            logger.warning("capture_jpeg error: %s", exc)
        return None

    def status(self) -> dict:
        s = self._status
        return {
            "active": s.active,
            "source_type": s.source_type,
            "source_id": s.source_id,
            "source_url": s.source_url,
            "fps": s.fps,
            "error": s.error,
            "resolution": s.resolution,
            "config_source_type": self._config.source_type,
            "config_manual_url": self._config.manual_url,
        }

    def list_sources(self) -> list[dict]:
        from camera_manager import CameraManager
        cameras = CameraManager.list_cameras()
        if self._droidcam_url:
            ip = (self._droidcam_url.split("/")[2].split(":")[0]
                  if "/" in self._droidcam_url else "?")
            cameras.append({
                "index": -1,
                "name": f"DroidCam ({ip})",
                "backend": "Network",
                "source": "droidcam",
                "url": self._droidcam_url,
            })
        return cameras

    def scan_droidcam(self) -> str | None:
        from network_scanner import scan_droidcam_fast
        url = scan_droidcam_fast()
        if url:
            self._droidcam_url = url
        return url

    def close(self) -> None:
        """Cierra la fuente actual."""
        with self._lock:
            self._close()

    def stop(self) -> None:
        """Alias de close()."""
        self.close()

    def _resolve(self, config: SourceConfig) -> cv2.VideoCapture | MjpegReader | None:
        if config.manual_url:
            cap = self._try_url(config.manual_url)
            if cap:
                self._status.source_type = "manual"
                self._status.source_id = config.manual_url
                self._status.source_url = config.manual_url
                return cap
            logger.warning("URL manual no disponible: %s", config.manual_url)

        detector = self._DETECTORS.get(config.source_type)
        if detector:
            cap = detector(self, config)
            if cap:
                return cap

        usb_detector = self._DETECTORS.get("usb")
        if usb_detector and config.source_type != "usb":
            cap = usb_detector(self, config)
            if cap:
                return cap

        if config.auto_discover:
            droidcam_detector = self._DETECTORS.get("droidcam")
            if droidcam_detector:
                cap = droidcam_detector(self, config)
                if cap:
                    return cap

        return None

    def _close(self) -> None:
        self._status.active = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._latest_frame = None
        logger.info("Fuente cerrada")

    def _try_url(self, url: str) -> cv2.VideoCapture | MjpegReader | None:
        return _try_url(url)


# ── Inicializar STREAM_PATHS desde network_scanner (opcional) ──────────
try:
    from network_scanner import STREAM_PATHS as _STREAM_PATHS
    SourceManager.STREAM_PATHS = _STREAM_PATHS
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════════
# DETECTORES
# ═══════════════════════════════════════════════════════════════════════

def _detect_manual(manager: SourceManager, config: SourceConfig
                   ) -> cv2.VideoCapture | MjpegReader | None:
    if config.manual_url:
        cap = _try_url(config.manual_url)
        if cap:
            manager._status.source_type = "manual"
            manager._status.source_id = config.manual_url
            manager._status.source_url = config.manual_url
            return cap
    return None


def _detect_usb(manager: SourceManager, config: SourceConfig
                ) -> cv2.VideoCapture | None:
    import platform
    is_windows = platform.system() == "Windows"

    backends = [(cv2.CAP_ANY, "Auto")]
    if is_windows:
        backends += [(cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF")]

    indices = [config.usb_index]
    if config.usb_index != 0:
        indices.append(0)
    for i in range(10):
        if i not in indices:
            indices.append(i)

    for idx in indices:
        for backend_id, backend_name in backends:
            try:
                cap = cv2.VideoCapture(idx, backend_id)
                # 720p: más detalle para rostros lejanos/pequeños → mejores
                # embeddings y reconocimiento más fiable (priorizamos calidad
                # sobre recursos). Si la cámara no soporta 1280x720, OpenCV
                # cae automáticamente a la resolución nativa más cercana.
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS, 30)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        logger.info("USB %d abierta con %s", idx, backend_name)
                        manager._status.source_type = "usb"
                        manager._status.source_id = str(idx)
                        manager._status.source_url = None
                        return cap
                cap.release()
            except Exception:
                pass
        time.sleep(0.1)
    return None


def _detect_droidcam(manager: SourceManager, config: SourceConfig
                     ) -> cv2.VideoCapture | MjpegReader | None:
    try:
        from network_scanner import scan_droidcam_fast
    except ImportError:
        return None

    base_url = manager._droidcam_url
    if not base_url:
        logger.info("DroidCam no cacheado. Buscando en red...")
        url = scan_droidcam_fast()
        if not url:
            logger.info("No se encontró DroidCam en la red")
            return None
        manager._droidcam_url = url
    else:
        url = base_url

    ip = url.split("/")[2].split(":")[0] if "/" in url else "?"
    cap = _try_url(url)
    if cap:
        logger.info("DroidCam conectado: %s", ip)
        manager._status.source_type = "droidcam"
        manager._status.source_id = ip
        manager._status.source_url = url
        return cap

    try:
        from network_scanner import DROIDCAM_PORT, DROIDCAM_URL_TEMPLATE
        for path in manager.STREAM_PATHS:
            alt_url = DROIDCAM_URL_TEMPLATE.format(ip, DROIDCAM_PORT, path)
            if alt_url == url:
                continue
            cap = _try_url(alt_url)
            if cap:
                manager._droidcam_url = alt_url
                manager._status.source_type = "droidcam"
                manager._status.source_id = ip
                manager._status.source_url = alt_url
                return cap
    except ImportError:
        pass

    return None


def _detect_rtsp(manager: SourceManager, config: SourceConfig
                 ) -> cv2.VideoCapture | MjpegReader | None:
    if config.manual_url and config.manual_url.startswith("rtsp://"):
        cap = _try_url(config.manual_url)
        if cap:
            manager._status.source_type = "rtsp"
            manager._status.source_id = config.manual_url
            manager._status.source_url = config.manual_url
            return cap
    return None


def _try_url(url: str) -> cv2.VideoCapture | MjpegReader | None:
    is_http = url.startswith(("http://", "https://"))
    backends = [cv2.CAP_FFMPEG, cv2.CAP_ANY]
    for backend_id in backends:
        try:
            cap = cv2.VideoCapture(url, backend_id)
            if not cap.isOpened():
                cap.release()
                continue
            for _ in range(3):
                ret, _ = cap.read()
                if ret:
                    return cap
            cap.release()
        except Exception:
            continue

    if is_http:
        try:
            reader = MjpegReader(url)
            if reader.isOpened():
                ret, frame = reader.read()
                if ret and frame is not None:
                    return reader
                reader.release()
        except Exception:
            pass

    return None


# ── Registrar detectores ───────────────────────────────────────────────
SourceManager._DETECTORS["manual"] = _detect_manual
SourceManager._DETECTORS["usb"] = _detect_usb
SourceManager._DETECTORS["droidcam"] = _detect_droidcam
SourceManager._DETECTORS["rtsp"] = _detect_rtsp
