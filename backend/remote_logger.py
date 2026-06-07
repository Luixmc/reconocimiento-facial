"""
Handler de logging que envía registros a Supabase en un hilo background.
Solo reenvía WARNING+ para no saturar la BD con logs de debug.
"""
from __future__ import annotations

import logging
import queue
import threading


class SupabaseLogHandler(logging.Handler):
    """
    Bufferiza logs en una queue y los envía a Supabase en un hilo daemon.
    Si la queue está llena o Supabase no responde, descarta silenciosamente.
    """

    def __init__(self, supabase, max_queue: int = 200) -> None:
        super().__init__(level=logging.WARNING)
        self._supabase = supabase
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait({
                "level": record.levelname,
                "message": self.format(record),
                "extra": {
                    "logger": record.name,
                    "module": record.module,
                    "funcName": record.funcName,
                },
            })
        except queue.Full:
            pass  # descartar: mejor perder un log que bloquear el hilo principal

    def _run(self) -> None:
        while True:
            try:
                entry = self._queue.get(timeout=5.0)
                self._supabase.push_log(
                    level=entry["level"],
                    message=entry["message"],
                    extra=entry["extra"],
                )
            except queue.Empty:
                continue
            except Exception:
                pass


def attach_remote_logging(supabase) -> None:
    """Agrega el handler remoto al logger raíz. Llamar una vez al arrancar."""
    handler = SupabaseLogHandler(supabase)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(handler)
