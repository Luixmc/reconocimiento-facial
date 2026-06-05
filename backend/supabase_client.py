"""
Cliente para interactuar con la API REST de Supabase.
Sube capturas faciales a Storage y registra accesos en la BD.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests

from config import (
    COMPANY_ID,
    CONFIDENCE_THRESHOLD,
    MODEL_NAME,
    MODEL_VERSION,
    STORAGE_BUCKET,
    SUPABASE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

@dataclass
class FaceMatchResult:
    person_id: str | None = None
    full_name: str = "Desconocido"
    confidence: float = 0.0
    similarity: float = 0.0
    matched_embedding_id: str | None = None
    authorized: bool = False


@dataclass
class AccessRecord:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = COMPANY_ID
    person_id: str | None = None
    occurred_at: str = field(default_factory=lambda: time.strftime(
        "%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime()
    ))
    result: str = "not_found"
    confidence: float = 0.0
    similarity: float = 0.0
    source_name: str = "camera_1"
    camera_kind: str = "usb"
    face_count: int = 1
    invalid_reason: str | None = None
    matched_face_embedding_id: str | None = None
    snapshot_url: str | None = None
    metadata: dict = field(default_factory=lambda: {
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "recognition_threshold": CONFIDENCE_THRESHOLD,
    })

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "company_id": self.company_id,
            "occurred_at": self.occurred_at,
            "result": self.result,
            "confidence": self.confidence,
            "similarity": self.similarity,
            "source_name": self.source_name,
            "camera_kind": self.camera_kind,
            "face_count": self.face_count,
            "invalid_reason": self.invalid_reason,
            "metadata": json.dumps(self.metadata),
        }
        if self.person_id:
            d["person_id"] = self.person_id
        if self.matched_face_embedding_id:
            d["matched_face_embedding_id"] = self.matched_face_embedding_id
        return d


# ---------------------------------------------------------------------------
# Cliente Supabase
# ---------------------------------------------------------------------------

class SupabaseClient:
    """Cliente liviano vía REST para operaciones de Supabase."""

    def __init__(self) -> None:
        self._headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }
        self._base_url = SUPABASE_URL.rstrip("/")

    # ── Storage ──────────────────────────────────────────────────────────

    def ensure_storage_bucket(self) -> bool:
        """Crea el bucket si no existe."""
        url = f"{self._base_url}/storage/v1/bucket"
        resp = requests.get(url, headers=self._headers, timeout=10)
        if not resp.ok:
            logger.warning("Error listando buckets: %s", resp.text)
            return False
        buckets = resp.json()
        if any(b["name"] == STORAGE_BUCKET for b in buckets):
            logger.info("Bucket '%s' ya existe", STORAGE_BUCKET)
            return True
        resp = requests.post(
            url,
            headers=self._headers,
            json={"name": STORAGE_BUCKET, "public": True},
            timeout=10,
        )
        if resp.ok:
            logger.info("Bucket '%s' creado correctamente", STORAGE_BUCKET)
            return True
        logger.warning("Error creando bucket: %s", resp.text)
        return False

    def upload_snapshot(self, image_bytes: bytes, filename: str | None = None) -> str | None:
        """
        Sube una imagen al bucket 'face-snapshots'.
        Retorna la URL pública de la imagen o None.
        """
        if filename is None:
            filename = f"snap_{uuid.uuid4().hex[:12]}.jpg"
        url = f"{self._base_url}/storage/v1/object/{STORAGE_BUCKET}/{filename}"
        upload_headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "image/jpeg",
        }
        resp = requests.post(url, headers=upload_headers, data=image_bytes, timeout=15)
        if resp.ok:
            public_url = (
                f"{self._base_url}/storage/v1/object/public/"
                f"{STORAGE_BUCKET}/{filename}"
            )
            logger.info("Snapshot subida: %s", public_url)
            return public_url
        logger.warning("Error subiendo snapshot: %s", resp.text)
        return None

    # ── Face Embeddings ──────────────────────────────────────────────────

    def fetch_face_embeddings(self) -> list[dict[str, Any]]:
        """
        Obtiene todos los embeddings activos de la empresa con el nombre
        de la persona mediante dos consultas (más fiable que el join de
        PostgREST).
        """
        url = f"{self._base_url}/rest/v1/face_embeddings"
        params = {
            "select": "id,person_id,embedding,embedding_dimension,status",
            "company_id": f"eq.{COMPANY_ID}",
            "status": "eq.active",
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if not resp.ok:
            logger.warning("Error fetching embeddings: %s", resp.text)
            return []

        records = resp.json()
        if not records:
            return []

        # Obtener nombres de las personas en un solo lote
        person_ids = list({r["person_id"] for r in records if r.get("person_id")})
        persons_map: dict[str, dict] = {}

        if person_ids:
            persons_url = f"{self._base_url}/rest/v1/registered_persons"
            person_params = {
                "select": "id,full_name,status",
                "id": f"in.({','.join(person_ids)})",
            }
            p_resp = requests.get(
                persons_url, headers=self._headers, params=person_params, timeout=10
            )
            if p_resp.ok:
                for p in p_resp.json():
                    persons_map[p["id"]] = p

        # Combinar datos
        enriched = []
        for r in records:
            person = persons_map.get(r.get("person_id", ""), {})
            r["person"] = {
                "full_name": person.get("full_name", "Desconocido"),
                "status": person.get("status", "active"),
            }
            enriched.append(r)

        return enriched

    def count_embeddings(self, person_id: str) -> int:
        """Cuenta cuántos embeddings activos tiene una persona."""
        url = f"{self._base_url}/rest/v1/face_embeddings"
        params = {
            "select": "id",
            "person_id": f"eq.{person_id}",
            "status": "eq.active",
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok:
            return len(resp.json())
        return 0

    # ── Registered Persons ──────────────────────────────────────────────

    def fetch_person_by_id(self, person_id: str) -> dict[str, Any] | None:
        """Obtiene datos de una persona por su ID."""
        url = f"{self._base_url}/rest/v1/registered_persons"
        params = {"id": f"eq.{person_id}", "select": "*"}
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok and resp.json():
            return resp.json()[0]
        return None

    # ── Access Records ──────────────────────────────────────────────────

    def insert_access_record(self, record: AccessRecord) -> bool:
        """Inserta un registro de acceso en la BD."""
        url = f"{self._base_url}/rest/v1/access_records"
        resp = requests.post(
            url,
            headers={**self._headers, "Prefer": "return=minimal"},
            json=record.to_dict(),
            timeout=10,
        )
        if resp.ok:
            logger.info(
                "Access record created: %s (%s · %s)",
                record.id, record.result, record.source_name,
            )
            return True
        logger.warning("Error inserting access record: %s", resp.text)
        return False

    # ── Attendances ─────────────────────────────────────────────────────

    def get_today_attendance(self, person_id: str) -> dict[str, Any] | None:
        """
        Busca una attendance abierta para la persona en la fecha de hoy.
        Retorna el registro o None.
        """
        today = time.strftime("%Y-%m-%d")
        url = f"{self._base_url}/rest/v1/attendances"
        params = {
            "select": "*",
            "person_id": f"eq.{person_id}",
            "attendance_date": f"eq.{today}",
            "status": "eq.open",
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok and resp.json():
            return resp.json()[0]
        return None

    def create_attendance(self, person_id: str) -> dict[str, Any] | None:
        """Crea una nueva attendance (jornada) para hoy. Retorna el registro."""
        today = time.strftime("%Y-%m-%d")
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime())
        attendance_id = str(uuid.uuid4())
        payload = {
            "id": attendance_id,
            "company_id": COMPANY_ID,
            "person_id": person_id,
            "attendance_date": today,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }
        url = f"{self._base_url}/rest/v1/attendances"
        resp = requests.post(
            url,
            headers={**self._headers, "Prefer": "return=representation"},
            json=payload,
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            logger.info("Attendance creada: %s para %s", attendance_id, person_id)
            return data[0] if isinstance(data, list) else data
        logger.warning("Error creando attendance: %s", resp.text)
        return None

    def close_attendance(self, attendance_id: str) -> bool:
        """Cierra una attendance (marca status='closed')."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime())
        url = f"{self._base_url}/rest/v1/attendances?id=eq.{attendance_id}"
        resp = requests.patch(
            url,
            headers=self._headers,
            json={"status": "closed", "updated_at": now},
            timeout=10,
        )
        if resp.ok:
            logger.info("Attendance cerrada: %s", attendance_id)
            return True
        logger.warning("Error cerrando attendance: %s", resp.text)
        return False

    # ── Attendance Marks ────────────────────────────────────────────────

    def insert_attendance_mark(
        self,
        attendance_id: str,
        access_record_id: str,
        mark_type: str,  # 'entry' o 'exit'
    ) -> bool:
        """Registra una marcación (entrada/salida) en attendance_marks."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime())
        mark_id = str(uuid.uuid4())
        payload = {
            "id": mark_id,
            "company_id": COMPANY_ID,
            "attendance_id": attendance_id,
            "access_record_id": access_record_id,
            "mark_type": mark_type,
            "marked_at": now,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        url = f"{self._base_url}/rest/v1/attendance_marks"
        resp = requests.post(
            url,
            headers={**self._headers, "Prefer": "return=minimal"},
            json=payload,
            timeout=10,
        )
        if resp.ok:
            logger.info("Mark %s registrada: %s", mark_type, mark_id)
            return True
        logger.warning("Error insertando attendance mark: %s", resp.text)
        return False
