"""
Lógica de negocio de asistencia: apertura/cierre de jornadas (entrada/salida).
Separado de main.py para facilitar testing y reutilización.
"""
from __future__ import annotations

import logging

from supabase_client import SupabaseClient

logger = logging.getLogger(__name__)


def handle_attendance(
    supabase: SupabaseClient,
    access_record_id: str,
    person_id: str | None,
) -> None:
    """
    Gestiona la apertura/cierre de attendances.
    - Sin attendance abierta hoy  → crea una (ENTRADA)
    - Con attendance abierta      → la cierra  (SALIDA)
    """
    if not person_id:
        return

    existing = supabase.get_today_attendance(person_id)

    if existing:
        att_id = existing["id"]
        if supabase.close_attendance(att_id):
            supabase.insert_attendance_mark(att_id, access_record_id, "exit")
            logger.info("SALIDA registrada — persona %s", person_id)
    else:
        att = supabase.create_attendance(person_id)
        if att:
            supabase.insert_attendance_mark(att["id"], access_record_id, "entry")
            logger.info("ENTRADA registrada — persona %s", person_id)
