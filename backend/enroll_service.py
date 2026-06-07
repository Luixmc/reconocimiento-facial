"""
Servicio de enrollment accesible vía HTTP para que Flutter (o cualquier cliente)
pueda enrolar personas sin ejecutar enrollment.py manualmente.

Endpoint: POST /api/enroll
  Multipart form-data:
    person_id  : UUID de registered_persons
    image      : archivo JPEG/PNG (frame de cámara)

Respuesta OK:
  {"ok": true, "embedding_id": "...", "person_name": "..."}

Respuesta error:
  {"ok": false, "error": "descripción"}
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import cv2
import numpy as np

from config import COMPANY_ID, MODEL_NAME, MODEL_VERSION, MIN_CONFIDENCE_TO_CAPTURE
from supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

try:
    from insightface.app import FaceAnalysis as _FaceAnalysis
    _HAS_INSIGHTFACE = True
except ImportError:
    _HAS_INSIGHTFACE = False


class EnrollService:
    """
    Procesa una imagen y registra el embedding facial en Supabase.
    Reutiliza el modelo InsightFace ya cargado en memoria si se pasa `face_app`.
    """

    def __init__(self, supabase: SupabaseClient, face_app=None) -> None:
        self._supabase = supabase
        self._app = face_app  # FaceAnalysis ya inicializado (del FaceDetector principal)

    def enroll_from_bytes(
        self,
        person_id: str,
        image_bytes: bytes,
    ) -> dict[str, Any]:
        """
        Detecta un rostro en image_bytes, extrae embedding y lo guarda en Supabase.
        Retorna dict con ok, embedding_id, person_name o error.
        """
        # Decodificar imagen
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"ok": False, "error": "No se pudo decodificar la imagen"}

        # Verificar persona existe
        person = self._supabase.fetch_person_by_id(person_id)
        if person is None:
            return {"ok": False, "error": f"Persona {person_id} no encontrada"}

        person_name = person.get("full_name", "Desconocido")

        # Detectar rostro
        if not _HAS_INSIGHTFACE or self._app is None:
            return {"ok": False, "error": "InsightFace no disponible en el servidor"}

        faces = self._app.get(frame)
        if not faces:
            return {"ok": False, "error": "No se detectó ningún rostro en la imagen"}
        if len(faces) > 1:
            return {"ok": False, "error": f"Se detectaron {len(faces)} rostros — envía solo 1 persona"}

        face = faces[0]
        if float(face.det_score) < MIN_CONFIDENCE_TO_CAPTURE:
            return {
                "ok": False,
                "error": f"Confianza baja ({face.det_score:.2f}) — mejor iluminación o ángulo",
            }

        embedding = face.embedding.tolist()

        # Subir snapshot a Storage
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        snap_url = None
        try:
            prefix = person_name.lower().replace(" ", "_")[:20]
            snap_url = self._supabase.upload_snapshot(buf.tobytes(), f"enroll_{prefix}_{uuid.uuid4().hex[:6]}.jpg")
        except Exception as exc:
            logger.warning("Snapshot de enrollment no subida: %s", exc)

        # Registrar embedding
        embedding_id = str(uuid.uuid4())
        existing_count = self._supabase.count_embeddings(person_id)
        is_primary = existing_count == 0
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": embedding_id,
            "company_id": COMPANY_ID,
            "person_id": person_id,
            "embedding": embedding,
            "embedding_dimension": len(embedding),
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "source": "api_enroll",
            "status": "active",
            "is_primary": is_primary,
            "created_at": now,
            "updated_at": now,
        }

        import requests as _req
        from config import SUPABASE_KEY, SUPABASE_URL
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/face_embeddings"
        resp = _req.post(url, headers=headers, json=payload, timeout=10)
        if not resp.ok:
            logger.error("Error insertando embedding: %s", resp.text)
            return {"ok": False, "error": f"Error guardando embedding: {resp.text[:200]}"}

        logger.info("Enrollment exitoso: %s → embedding %s", person_name, embedding_id)
        return {
            "ok": True,
            "embedding_id": embedding_id,
            "person_id": person_id,
            "person_name": person_name,
            "confidence": float(face.det_score),
            "snapshot_url": snap_url,
            "is_primary": is_primary,
        }
