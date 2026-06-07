"""
Módulo de detección y reconocimiento facial usando InsightFace.
Detecta rostros en frames de cámara y busca coincidencias con los
embeddings almacenados en Supabase.
"""
from __future__ import annotations

import json
import logging
import pickle
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config import MODEL_NAME, MODEL_VERSION
from config import AUTHORIZED_SIMILARITY, MATCH_MINIMUM_SIMILARITY
from supabase_client import FaceMatchResult, SupabaseClient

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent / "embeddings_cache.pkl"

try:
    import insightface
    from insightface.app import FaceAnalysis

    HAS_INSIGHTFACE = True
except ImportError:
    HAS_INSIGHTFACE = False
    logger.warning("InsightFace no disponible. Se usará detección básica con OpenCV.")


class FaceDetector:
    """
    Detector/reconocedor facial.
    - En modo InsightFace: detecta + extrae embeddings + compara con BD.
    - Fallback OpenCV: solo detecta rostros (sin reconocer).
    """

    def __init__(self, supabase: SupabaseClient) -> None:
        self._supabase = supabase
        self._app: FaceAnalysis | None = None
        self._known_embeddings: list[dict[str, Any]] = []
        self._emb_matrix: np.ndarray | None = None
        self._embeddings_lock = threading.Lock()
        self._ready = False

        # Inicializar modelo
        self._init_model()

    def _init_model(self) -> None:
        """Inicializa InsightFace. Si falla, modo degradado con Haar Cascade."""
        if not HAS_INSIGHTFACE:
            logger.warning("Usando modo degradado (Haar Cascade)")
            self._cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            self._ready = True
            return

        try:
            self._app = FaceAnalysis(
                name="buffalo_s",
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            self._app.prepare(ctx_id=0, det_thresh=0.5)
            logger.info("InsightFace buffalo_s inicializado correctamente")
            self._ready = True
            # Cargar embeddings conocidos
            self._load_known_embeddings()
        except Exception as exc:
            logger.exception("Error inicializando InsightFace: %s", exc)
            self._app = None
            # Fallback a Haar Cascade
            self._cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            self._ready = True

    def _parse_records(self, records: list[dict]) -> list[dict]:
        parsed = []
        for r in records:
            emb_str = r.get("embedding", "[]")
            if isinstance(emb_str, str):
                emb = np.array(json.loads(emb_str), dtype=np.float32)
            else:
                emb = np.array(emb_str, dtype=np.float32)
            person_data = r.get("person", {})
            full_name = person_data.get("full_name", "Desconocido") if isinstance(person_data, dict) else "Desconocido"
            parsed.append({
                "embedding_id": r["id"],
                "person_id": r["person_id"],
                "full_name": full_name,
                "embedding": emb,
            })
        return parsed

    def _load_known_embeddings(self) -> None:
        """Carga embeddings desde Supabase (o caché en disco si Supabase falla)."""
        if not HAS_INSIGHTFACE or self._app is None:
            return
        try:
            records = self._supabase.fetch_face_embeddings()
            parsed = self._parse_records(records)
            with self._embeddings_lock:
                self._known_embeddings = parsed
                self._build_matrix()
            self._save_cache(parsed)
            logger.info("Cargados %d embeddings desde Supabase", len(parsed))
        except Exception as exc:
            logger.exception("Error cargando embeddings de Supabase: %s", exc)
            self._load_from_cache()

    def _build_matrix(self) -> None:
        """Pre-computa matriz normalizada para búsqueda vectorizada. Llamar con lock tomado."""
        if not self._known_embeddings:
            self._emb_matrix: np.ndarray | None = None
            return
        raw = np.stack([e["embedding"] for e in self._known_embeddings])  # (N, 512)
        norms = np.linalg.norm(raw, axis=1, keepdims=True) + 1e-10
        self._emb_matrix = raw / norms  # normalizada, float32

    def _save_cache(self, parsed: list[dict]) -> None:
        try:
            with open(_CACHE_PATH, "wb") as f:
                pickle.dump(parsed, f)
        except Exception as exc:
            logger.debug("No se pudo guardar caché: %s", exc)

    def _load_from_cache(self) -> None:
        if not _CACHE_PATH.exists():
            return
        try:
            with open(_CACHE_PATH, "rb") as f:
                parsed = pickle.load(f)
            with self._embeddings_lock:
                self._known_embeddings = parsed
                self._build_matrix()
            logger.info("Embeddings cargados desde caché local (%d)", len(parsed))
        except Exception as exc:
            logger.warning("Caché inválida: %s", exc)

    def refresh_embeddings(self) -> None:
        """Recarga embeddings desde Supabase e invalida caché."""
        self._load_known_embeddings()

    # ── Detección ──────────────────────────────────────────────────────

    def detect_faces(self, frame: np.ndarray) -> list[dict[str, Any]]:
        """
        Detecta rostros en un frame.
        Reduce resolución a 320×240 antes de detectar para acelerar ~4x,
        luego re-escala los bounding boxes a las coordenadas originales.
        Retorna lista de dicts con 'bbox', 'confidence' y (si es posible) 'embedding'.
        """
        if not self._ready:
            return []

        if self._app is not None:
            # ── Downscale para acelerar detección ────────────────────────
            h, w = frame.shape[:2]
            target_w = 320
            scale = target_w / w
            target_h = int(h * scale)
            if scale < 1.0:
                small_frame = cv2.resize(frame, (target_w, target_h),
                                          interpolation=cv2.INTER_LINEAR)
            else:
                small_frame = frame

            # InsightFace sobre frame reducido
            faces = self._app.get(small_frame)
            results = []
            for face in faces:
                # Re-escalar bbox a coordenadas originales
                bbox = face.bbox.astype(int).tolist()
                if scale < 1.0:
                    inv_scale = 1.0 / scale
                    bbox = [
                        int(bbox[0] * inv_scale),
                        int(bbox[1] * inv_scale),
                        int(bbox[2] * inv_scale),
                        int(bbox[3] * inv_scale),
                    ]
                results.append({
                    "bbox": bbox,
                    "confidence": float(face.det_score),
                    "embedding": face.embedding,
                    "landmarks": face.landmark.astype(int).tolist() if face.landmark is not None else None,
                })
            return results
        else:
            # Fallback Haar Cascade
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self._cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
            )
            results = []
            for x, y, w, h in faces:
                results.append({
                    "bbox": [x, y, x + w, y + h],
                    "confidence": 0.9,
                    "embedding": None,
                })
            return results

    # ── Reconocimiento ──────────────────────────────────────────────────

    def recognize(self, embedding: np.ndarray) -> FaceMatchResult:
        """
        Compara un embedding contra todos los conocidos (vectorizado).
        Un solo dot-product matricial en vez de loop.
        """
        if not HAS_INSIGHTFACE or self._app is None:
            return FaceMatchResult()

        with self._embeddings_lock:
            if not self._known_embeddings or self._emb_matrix is None:
                return FaceMatchResult()

            # Normalizar query
            norm_e = embedding / (np.linalg.norm(embedding) + 1e-10)  # (512,)
            # Similitudes coseno de todos en una operación
            sims = self._emb_matrix @ norm_e  # (N,)
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])

            if best_sim > MATCH_MINIMUM_SIMILARITY:
                best_match = self._known_embeddings[best_idx]
                confidence = max(
                    0.0,
                    (best_sim - MATCH_MINIMUM_SIMILARITY)
                    / (1.0 - MATCH_MINIMUM_SIMILARITY),
                )
                return FaceMatchResult(
                    person_id=best_match["person_id"],
                    full_name=best_match["full_name"],
                    confidence=min(confidence, 1.0),
                    similarity=best_sim,
                    matched_embedding_id=best_match["embedding_id"],
                    authorized=best_sim > AUTHORIZED_SIMILARITY,
                )

        return FaceMatchResult()

    # ── Dibujo ─────────────────────────────────────────────────────────

    @staticmethod
    def draw_detections(
        frame: np.ndarray,
        faces: list[dict[str, Any]],
        match: FaceMatchResult | None = None,
    ) -> np.ndarray:
        """Dibuja bounding boxes y etiquetas en el frame."""
        output = frame.copy()

        for face in faces:
            bbox = face["bbox"]
            x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]

            if match and match.person_id and face is faces[0]:
                color = (0, 255, 0) if match.authorized else (0, 165, 255)
                label = f"{match.full_name} ({match.similarity:.2f})"
            else:
                color = (255, 255, 0)
                label = "Detectando..."

            cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                output, label, (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2,
            )

        return output
