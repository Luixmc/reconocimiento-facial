"""
Verificaciones al arrancar:
  - License: valida que la empresa tenga una licencia activa en Supabase.
  - OTA:     compara la versión local con la última publicada en Supabase.

Ambas son no-bloqueantes: si Supabase no responde, el backend arranca igual.
"""
from __future__ import annotations

import logging
import os

import requests

from config import COMPANY_ID, SERVICE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

# Versión de este build — incrementar en cada release
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")


# ── License ───────────────────────────────────────────────────────────────────

def check_license() -> bool:
    """
    Retorna True si hay una licencia activa para COMPANY_ID.
    Si la tabla no existe o Supabase no responde, retorna True (fail-open)
    para no bloquear el arranque en desarrollo.
    """
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/licenses"
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
    }
    params = {
        "select": "id,status,expires_at",
        "company_id": f"eq.{COMPANY_ID}",
        "status": "eq.active",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        if not resp.ok:
            # Tabla no existe aún → modo desarrollo, continuar
            logger.warning("License check: tabla no encontrada (%s) — continuando", resp.status_code)
            return True
        licenses = resp.json()
        if licenses:
            logger.info("Licencia activa: %s", licenses[0].get("id", "?"))
            return True
        logger.warning("No hay licencia activa para COMPANY_ID=%s", COMPANY_ID)
        return False
    except Exception as exc:
        logger.warning("License check falló (sin conexión?): %s — continuando", exc)
        return True  # fail-open


# ── OTA ───────────────────────────────────────────────────────────────────────

def check_for_update() -> dict | None:
    """
    Consulta la tabla `app_versions` en Supabase por la última versión publicada.
    Retorna un dict {"version": str, "download_url": str, "release_notes": str}
    si hay una versión más nueva, o None si estamos al día o sin conexión.
    """
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_versions"
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
    }
    params = {
        "select": "version,download_url,release_notes,is_mandatory",
        "order": "created_at.desc",
        "limit": "1",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        if not resp.ok or not resp.json():
            return None
        latest = resp.json()[0]
        latest_ver = latest.get("version", "0.0.0")
        if _version_gt(latest_ver, APP_VERSION):
            logger.warning(
                "Nueva versión disponible: %s (actual: %s) — %s",
                latest_ver, APP_VERSION,
                latest.get("download_url", "sin URL"),
            )
            return latest
        logger.info("Versión actualizada: %s", APP_VERSION)
        return None
    except Exception as exc:
        logger.debug("OTA check falló: %s", exc)
        return None


def _version_gt(a: str, b: str) -> bool:
    """Retorna True si versión a > b (comparación semver simple)."""
    def _parts(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0, 0, 0)
    return _parts(a) > _parts(b)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def run_startup_checks() -> bool:
    """
    Ejecuta todas las verificaciones de arranque.
    Retorna False si hay un bloqueante crítico (licencia expirada).
    """
    logger.info("Ejecutando verificaciones de arranque...")

    # License
    license_ok = check_license()
    if not license_ok:
        logger.error("LICENCIA INVÁLIDA — el sistema no arrancará en producción.")
        # En MVP: solo advertir. En producción: retornar False para bloquear.
        # return False

    # OTA
    update = check_for_update()
    if update and update.get("is_mandatory"):
        logger.error(
            "Actualización OBLIGATORIA disponible (%s). Descarga: %s",
            update["version"], update.get("download_url"),
        )

    return True  # fail-open hasta tener infraestructura de licencias completa
