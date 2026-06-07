"""
Cola offline con SQLite local.
Encola registros cuando Supabase no está disponible y los sube cuando
vuelve la conexión (intenta cada 10 minutos).
"""
from __future__ import annotations

import json
import logging
import socket
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase_client import SupabaseClient

logger = logging.getLogger("offline_queue")

_DB_FILE = Path(__file__).parent / "offline_records.db"


class OfflineQueue:
    """
    Cola SQLite persistente para access_records cuando Supabase no responde.
    Hilo daemon intenta vaciar la cola cada SYNC_INTERVAL_SECONDS.
    """

    SYNC_INTERVAL_SECONDS = 600  # 10 minutos

    def __init__(self, db_path: Path = _DB_FILE) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._supabase: SupabaseClient | None = None
        self._sync_thread: threading.Thread | None = None
        self._init_db()
        logger.info("OfflineQueue inicializada en %s", db_path)

    # ── Setup ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_records (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type  TEXT    NOT NULL,
                    payload      TEXT    NOT NULL,
                    created_at   TEXT    NOT NULL,
                    attempts     INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), check_same_thread=False)

    # ── API pública ──────────────────────────────────────────────────────────

    def enqueue(self, record_type: str, payload: dict) -> None:
        """Encola un registro para subida posterior."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO pending_records (record_type, payload, created_at) VALUES (?, ?, ?)",
                    (record_type, json.dumps(payload), datetime.utcnow().isoformat()),
                )
                conn.commit()
        logger.debug("Encolado offline: %s (total=%d)", record_type, self.pending_count())

    def pending_count(self) -> int:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) FROM pending_records").fetchone()
                return row[0] if row else 0

    def db_path(self) -> str:
        return str(self._db_path)

    def purge_all(self) -> int:
        """Elimina TODOS los registros pendientes. Usar con precaución."""
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM pending_records")
                conn.commit()
                return cur.rowcount

    # ── Daemon de sincronización ─────────────────────────────────────────────

    def start_sync_daemon(self, supabase_client: SupabaseClient) -> None:
        """Inicia el hilo daemon que intenta sincronizar cada SYNC_INTERVAL_SECONDS."""
        self._supabase = supabase_client
        self._stop.clear()
        self._sync_thread = threading.Thread(
            target=self._sync_loop,
            daemon=True,
            name="offline-sync",
        )
        self._sync_thread.start()
        logger.info(
            "Daemon offline-sync iniciado (cada %ds)",
            self.SYNC_INTERVAL_SECONDS,
        )

    def stop(self) -> None:
        self._stop.set()

    def try_sync_now(self) -> int:
        """Fuerza un intento de sincronización inmediato. Retorna registros enviados."""
        if not self._supabase:
            return 0
        if not self._has_internet():
            logger.debug("Sin internet — sync cancelado")
            return 0
        return self._flush()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _sync_loop(self) -> None:
        while not self._stop.wait(timeout=self.SYNC_INTERVAL_SECONDS):
            if self._has_internet() and self.pending_count() > 0:
                logger.info(
                    "Sincronizando %d registros offline...", self.pending_count()
                )
                sent = self._flush()
                logger.info("Sincronizados %d registros offline.", sent)

    def _has_internet(self) -> bool:
        try:
            socket.setdefaulttimeout(3)
            with socket.create_connection(("8.8.8.8", 53)):
                return True
        except OSError:
            return False

    def _flush(self) -> int:
        """Envía hasta 100 registros a Supabase. Retorna cantidad enviada."""
        if not self._supabase:
            return 0

        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, record_type, payload FROM pending_records "
                    "ORDER BY id LIMIT 100"
                ).fetchall()

        sent_ids: list[int] = []
        for row_id, record_type, payload_str in rows:
            try:
                payload = json.loads(payload_str)
                if record_type == "access_record":
                    ok = self._supabase.insert_access_record_raw(payload)
                    if ok:
                        sent_ids.append(row_id)
                    else:
                        self._increment_attempts(row_id)
                else:
                    # Tipo desconocido — marcar como procesado igual
                    sent_ids.append(row_id)
            except Exception as exc:
                logger.warning("Error procesando registro offline %d: %s", row_id, exc)
                self._increment_attempts(row_id)

        if sent_ids:
            with self._lock:
                with self._connect() as conn:
                    placeholders = ",".join("?" * len(sent_ids))
                    conn.execute(
                        f"DELETE FROM pending_records WHERE id IN ({placeholders})",
                        sent_ids,
                    )
                    conn.commit()

        return len(sent_ids)

    def _increment_attempts(self, row_id: int) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE pending_records SET attempts = attempts + 1 WHERE id = ?",
                    (row_id,),
                )
                # Descartar registros con demasiados reintentos (>10)
                conn.execute(
                    "DELETE FROM pending_records WHERE id = ? AND attempts > 10",
                    (row_id,),
                )
                conn.commit()
