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
from config import (
    AMBIGUITY_MARGIN,
    AUTHORIZED_SIMILARITY,
    MATCH_MINIMUM_SIMILARITY,
    MIN_FACE_PIXELS,
)
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
            # IMPORTANTE: debe coincidir con el modelo usado en enrollment.py
            # (buffalo_l). Si difieren, los embeddings viven en espacios
            # vectoriales distintos y la similitud da ~0 → todo "Desconocido".
            self._app = FaceAnalysis(
                name="buffalo_l",
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            # det_size 800×800 = detector trabaja sobre una grilla más fina,
            # mejor para rostros pequeños/lejanos en frames de 720p (priorizamos
            # precisión sobre recursos). det_thresh 0.5 filtra detecciones débiles.
            self._app.prepare(ctx_id=0, det_thresh=0.5, det_size=(800, 800))
            logger.info("InsightFace buffalo_l inicializado correctamente")
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

    def clear_face_cache(self) -> dict:
        """
        Vacía la caché interna de rostros (embeddings_cache.pkl) y los
        embeddings en memoria, luego intenta recargar fresco desde Supabase.
        Útil tras re-registrar personas o cambiar de modelo.
        Retorna {ok, embeddings_loaded, cache_deleted}.
        """
        cache_deleted = False
        try:
            if _CACHE_PATH.exists():
                _CACHE_PATH.unlink()
                cache_deleted = True
        except Exception as exc:
            logger.warning("No se pudo borrar la caché de rostros: %s", exc)

        # Vaciar memoria
        with self._embeddings_lock:
            self._known_embeddings = []
            self._emb_matrix = None

        # Recargar fresco desde Supabase (regenera la caché)
        self._load_known_embeddings()
        with self._embeddings_lock:
            loaded = len(self._known_embeddings)

        logger.info("Caché de rostros vaciada (borrada=%s) — recargados %d embeddings",
                    cache_deleted, loaded)
        return {"ok": True, "embeddings_loaded": loaded, "cache_deleted": cache_deleted}

    # ── Detección ──────────────────────────────────────────────────────

    def detect_faces(self, frame: np.ndarray) -> list[dict[str, Any]]:
        """
        Detecta rostros en un frame.
        Para máxima fiabilidad procesa a resolución nativa hasta 1280px de
        ancho (720p), sin reducir la calidad de los recortes que InsightFace
        usa para extraer los embeddings — más detalle = mejor reconocimiento
        de rostros lejanos/pequeños. Solo reescala hacia abajo si el frame
        de la cámara es más grande que eso (para no disparar el costo de
        cómputo sin necesidad). Re-escala los bounding boxes resultantes a
        las coordenadas originales y descarta rostros demasiado pequeños,
        cuyo embedding sería poco fiable y daría falsos positivos.
        Retorna lista de dicts con 'bbox', 'confidence' y (si es posible) 'embedding'.
        """
        if not self._ready:
            return []

        if self._app is not None:
            # ── Procesar a resolución nativa, hasta 1280px (720p) de ancho ──
            h, w = frame.shape[:2]
            target_w = 1280
            scale = target_w / w
            if scale < 1.0:
                target_h = int(h * scale)
                small_frame = cv2.resize(frame, (target_w, target_h),
                                          interpolation=cv2.INTER_LINEAR)
            else:
                # Frame ya es <=1280px: usar tal cual (no interpolar hacia arriba)
                scale = 1.0
                small_frame = frame

            # InsightFace sobre el frame escalado
            faces = self._app.get(small_frame)
            results = []
            for face in faces:
                # Re-escalar bbox a coordenadas originales
                bbox = face.bbox.astype(int).tolist()
                if scale < 1.0:
                    inv_scale = 1.0 / scale
                    bbox = [int(c * inv_scale) for c in bbox]

                # Filtro de tamaño mínimo: rostros < MIN_FACE_PIXELS de ancho
                # están demasiado lejos para un reconocimiento confiable.
                face_w = bbox[2] - bbox[0]
                if face_w < MIN_FACE_PIXELS:
                    continue

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
                best_person = best_match["person_id"]

                # ── Test de ambigüedad ───────────────────────────────────
                # Mejor similitud de OTRA persona distinta. Si está demasiado
                # cerca del mejor match, la identidad es ambigua (dos personas
                # parecidas) → no autorizar para evitar falsos positivos.
                authorized = best_sim > AUTHORIZED_SIMILARITY
                runner_up = -1.0
                for i, e in enumerate(self._known_embeddings):
                    if e["person_id"] != best_person and float(sims[i]) > runner_up:
                        runner_up = float(sims[i])
                if authorized and runner_up > 0 and (best_sim - runner_up) < AMBIGUITY_MARGIN:
                    logger.info(
                        "Match ambiguo (best=%.3f vs 2º=%.3f) — no autorizado",
                        best_sim, runner_up,
                    )
                    authorized = False

                confidence = max(
                    0.0,
                    (best_sim - MATCH_MINIMUM_SIMILARITY)
                    / (1.0 - MATCH_MINIMUM_SIMILARITY),
                )
                return FaceMatchResult(
                    person_id=best_person,
                    full_name=best_match["full_name"],
                    confidence=min(confidence, 1.0),
                    similarity=best_sim,
                    matched_embedding_id=best_match["embedding_id"],
                    authorized=authorized,
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
