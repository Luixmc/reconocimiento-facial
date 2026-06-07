"""
Cliente para interactuar con la API REST de Supabase.
Sube capturas faciales a Storage y registra accesos en la BD.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any

import requests

from config import (
    COMPANY_ID,
    CONFIDENCE_THRESHOLD,
    MODEL_NAME,
    MODEL_VERSION,
    SERVICE_KEY,
    STORAGE_BUCKET,
    SUPABASE_URL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------

class SupabaseError(Exception):
    """Error base de operaciones con Supabase."""

class SupabaseUploadError(SupabaseError):
    """Fallo al subir un archivo a Storage."""

class EmbeddingFetchError(SupabaseError):
    """Fallo al obtener embeddings de la BD."""

class AttendanceError(SupabaseError):
    """Fallo en operaciones de attendance."""


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
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
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
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
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
        raise SupabaseUploadError(f"HTTP {resp.status_code}: {resp.text}")

    def cleanup_old_snapshots(self, max_age_hours: float = 24.0) -> int:
        """
        Borra del bucket las snapshots más viejas que `max_age_hours`.
        Solo sirven para que la persona vea cómo quedó su registro el mismo
        día — no hace falta conservarlas indefinidamente. Retorna cuántas
        se borraron.
        """
        list_url = f"{self._base_url}/storage/v1/object/list/{STORAGE_BUCKET}"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        to_delete: list[str] = []
        offset = 0
        limit = 100
        try:
            while True:
                resp = requests.post(
                    list_url,
                    headers=self._headers,
                    json={"limit": limit, "offset": offset,
                          "sortBy": {"column": "created_at", "order": "asc"}},
                    timeout=15,
                )
                if not resp.ok:
                    logger.warning("No se pudo listar snapshots para limpieza: %s", resp.text)
                    break
                items = resp.json()
                if not items:
                    break
                for item in items:
                    name = item.get("name")
                    created_raw = item.get("created_at")
                    if not name or not created_raw:
                        continue
                    try:
                        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if created < cutoff:
                        to_delete.append(name)
                if len(items) < limit:
                    break
                offset += limit
        except Exception as exc:
            logger.warning("Error listando snapshots para limpieza: %s", exc)
            return 0

        if not to_delete:
            return 0

        delete_url = f"{self._base_url}/storage/v1/object/{STORAGE_BUCKET}"
        deleted = 0
        for i in range(0, len(to_delete), 100):
            batch = to_delete[i:i + 100]
            try:
                resp = requests.delete(delete_url, headers=self._headers,
                                       json={"prefixes": batch}, timeout=20)
                if resp.ok:
                    deleted += len(batch)
                else:
                    logger.warning("Error borrando snapshots: %s", resp.text)
            except Exception as exc:
                logger.warning("Error borrando lote de snapshots: %s", exc)

        if deleted:
            logger.info("Limpieza de snapshots: %d archivo(s) borrado(s) (>%.0fh)", deleted, max_age_hours)
        return deleted

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
            raise EmbeddingFetchError(f"HTTP {resp.status_code}: {resp.text}")

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

    def create_person(
        self,
        full_name: str,
        document_number: str | None,
        position: str | None,
        created_by_operator_id: str | None,
    ) -> dict[str, Any] | None:
        """Crea una nueva persona registrada (registered_persons). Retorna el registro creado."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime())
        payload = {
            "id": str(uuid.uuid4()),
            "company_id": COMPANY_ID,
            "full_name": full_name,
            "document_number": document_number,
            "position": position,
            "status": "active",
            "created_by_operator_id": created_by_operator_id,
            "created_at": now,
            "updated_at": now,
        }
        url = f"{self._base_url}/rest/v1/registered_persons"
        resp = requests.post(
            url,
            headers={**self._headers, "Prefer": "return=representation"},
            json=payload,
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            person = data[0] if isinstance(data, list) else data
            logger.info("Persona creada: %s (%s)", full_name, person.get("id"))
            return person
        logger.warning("Error creando persona: %s", resp.text)
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

    # ── Remote Logging ──────────────────────────────────────────────────

    def push_log(self, level: str, message: str, extra: dict | None = None) -> None:
        """Envía una línea de log a la tabla backend_logs en Supabase."""
        payload = {
            "id": str(uuid.uuid4()),
            "company_id": COMPANY_ID,
            "level": level,
            "message": message[:2000],
            "extra": extra or {},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime()),
        }
        url = f"{self._base_url}/rest/v1/backend_logs"
        try:
            requests.post(
                url,
                headers={**self._headers, "Prefer": "return=minimal"},
                json=payload,
                timeout=5,
            )
        except Exception:
            pass  # logging no debe romper el flujo principal

    # ── Multi-tenant RPC ────────────────────────────────────────────────

    def set_company_context(self) -> None:
        """
        Llama set_company_context(COMPANY_ID) en Supabase vía RPC.
        Útil antes de operaciones en batch que invocan funciones/triggers
        con SECURITY DEFINER que leen app.company_id.
        Service_role ya bypassa RLS, pero los triggers pueden necesitar el contexto.
        """
        url = f"{self._base_url}/rest/v1/rpc/set_company_context"
        try:
            requests.post(
                url,
                headers=self._headers,
                json={"p_company_id": COMPANY_ID},
                timeout=5,
            )
        except Exception as exc:
            logger.debug("set_company_context falló (no crítico): %s", exc)

    # ── Admin queries ────────────────────────────────────────────────────

    def fetch_recent_access_records(self, limit: int = 50) -> list[dict]:
        """Devuelve los access_records más recientes de la empresa."""
        url = f"{self._base_url}/rest/v1/access_records"
        params = {
            "select": "id,person_id,occurred_at,result,confidence,similarity,source_name,snapshot_url",
            "company_id": f"eq.{COMPANY_ID}",
            "order": "occurred_at.desc",
            "limit": str(limit),
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok:
            return resp.json()
        logger.warning("fetch_recent_access_records error: %s", resp.text)
        return []

    def fetch_today_attendances(self) -> list[dict]:
        """Devuelve las asistencias de hoy con datos de la persona."""
        today = time.strftime("%Y-%m-%d")
        url = f"{self._base_url}/rest/v1/attendances"
        params = {
            "select": "id,person_id,attendance_date,status,created_at,updated_at",
            "company_id": f"eq.{COMPANY_ID}",
            "attendance_date": f"eq.{today}",
            "order": "created_at.desc",
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok:
            rows = resp.json()
            # Enriquecer con nombre de persona
            pid_set = {r["person_id"] for r in rows if r.get("person_id")}
            if pid_set:
                persons_url = f"{self._base_url}/rest/v1/registered_persons"
                p_resp = requests.get(
                    persons_url,
                    headers=self._headers,
                    params={"select": "id,full_name", "id": f"in.({','.join(pid_set)})"},
                    timeout=10,
                )
                if p_resp.ok:
                    pmap = {p["id"]: p["full_name"] for p in p_resp.json()}
                    for r in rows:
                        r["full_name"] = pmap.get(r.get("person_id", ""), "Desconocido")
            return rows
        logger.warning("fetch_today_attendances error: %s", resp.text)
        return []

    # ── Device management ────────────────────────────────────────────────────

    def upsert_device(
        self,
        device_uid: str,
        company_id: str,
        name: str,
        app_version: str | None = None,
    ) -> bool:
        """Registra o actualiza la terminal en Supabase vía RPC."""
        url = f"{self._base_url}/rest/v1/rpc/upsert_device"
        payload = {
            "p_device_uid": device_uid,
            "p_company_id": company_id,
            "p_name": name,
            "p_app_version": app_version,
        }
        try:
            resp = requests.post(url, headers=self._headers, json=payload, timeout=10)
            return resp.ok
        except Exception as exc:
            logger.warning("upsert_device error: %s", exc)
            return False

    def heartbeat_device(
        self,
        device_uid: str,
        detections_today: int = 0,
        app_version: str | None = None,
    ) -> bool:
        """Actualiza is_online=true + last_seen_at + detections_today."""
        url = f"{self._base_url}/rest/v1/rpc/heartbeat_device"
        try:
            resp = requests.post(
                url,
                headers=self._headers,
                json={
                    "p_device_uid": device_uid,
                    "p_detections_today": detections_today,
                    "p_app_version": app_version,
                },
                timeout=8,
            )
            return resp.ok
        except Exception as exc:
            logger.debug("heartbeat_device error: %s", exc)
            return False

    def device_offline(self, device_uid: str) -> None:
        """Marca el dispositivo como offline al apagar."""
        url = f"{self._base_url}/rest/v1/devices"
        try:
            requests.patch(
                url,
                headers=self._headers,
                params={"device_uid": f"eq.{device_uid}"},
                json={"is_online": False},
                timeout=5,
            )
        except Exception:
            pass

    def get_device_config(self, device_uid: str, company_id: str) -> dict | None:
        """Obtiene camera_configs + system_settings vía RPC."""
        url = f"{self._base_url}/rest/v1/rpc/get_device_config"
        try:
            resp = requests.post(
                url,
                headers=self._headers,
                json={"p_device_uid": device_uid, "p_company_id": company_id},
                timeout=8,
            )
            if resp.ok:
                return resp.json()
        except Exception as exc:
            logger.debug("get_device_config error: %s", exc)
        return None

    def insert_access_record_raw(self, record_dict: dict) -> bool:
        """Inserta un access_record desde un dict (para la cola offline)."""
        url = f"{self._base_url}/rest/v1/access_records"
        try:
            resp = requests.post(
                url,
                headers={**self._headers, "Prefer": "return=minimal"},
                json=record_dict,
                timeout=10,
            )
            return resp.ok
        except Exception as exc:
            logger.warning("insert_access_record_raw error: %s", exc)
            return False

    def check_connection(self) -> bool:
        """
        Comprueba conectividad REAL con Supabase: hace una consulta liviana a
        la tabla `companies` filtrando por COMPANY_ID. A diferencia de pegarle
        a la raíz /rest/v1/ (que responde 200 aunque la key no tenga acceso a
        datos), esto confirma que de verdad podemos leer/escribir la BD —
        evita el falso "online" cuando los inserts en realidad fallan.
        """
        try:
            resp = requests.head(
                f"{self._base_url}/rest/v1/companies",
                headers={**self._headers, "Prefer": "count=exact"},
                params={"id": f"eq.{COMPANY_ID}", "select": "id"},
                timeout=4,
            )
            return resp.ok
        except Exception:
            return False

    # ── Remote logging ────────────────────────────────────────────────────────

    def fetch_recent_logs(self, limit: int = 100) -> list[dict]:
        """Devuelve los últimos logs remotos de la empresa."""
        url = f"{self._base_url}/rest/v1/backend_logs"
        params = {
            "select": "id,level,message,extra,created_at",
            "company_id": f"eq.{COMPANY_ID}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok:
            return resp.json()
        logger.warning("fetch_recent_logs error: %s", resp.text)
        return []

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
