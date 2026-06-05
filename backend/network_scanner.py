"""
Módulo de autodescubrimiento de DroidCam en la red local.
Escanea el rango .1-.254 en busca del puerto HTTP 4747 de DroidCam
usando hilos en paralelo para completar en ~1-2 segundos.
"""
from __future__ import annotations

import concurrent.futures
import logging
import socket
from typing import Callable

logger = logging.getLogger(__name__)

DROIDCAM_PORT = 4747
DROIDCAM_PATH = "/video"
DROIDCAM_URL_TEMPLATE = "http://{}:{}{}"
SCAN_TIMEOUT = 0.3  # segundos por IP

# Endpoints de video a probar (ordenados por probabilidad)
STREAM_PATHS = ["/video", "/mjpeg", "/cam/1/frame.jpg", "/cam/1/mjpeg", "/stream.mjpeg", "/"]


def get_local_ip() -> str:
    """
    Obtiene la dirección IP local activa (la que usa para salir a Internet).
    Conecta un socket UDP a 8.8.8.8:80 (sin enviar datos) solo para
    determinar qué interfaz de red se usa para tráfico externo.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        # No envía datos — solo registra la ruta para determinar la IP de salida
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as exc:
        logger.debug("No se pudo obtener IP local: %s", exc)
        return "192.168.1.1"  # fallback genérico


def get_local_ip_base() -> str:
    """
    Obtiene los primeros 3 octetos de la IP local (ej: '192.168.1.').
    """
    ip = get_local_ip()
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}."
    return "192.168.1."


def _check_ip(ip: str) -> str | None:
    """
    Verifica si una IP tiene DroidCam activo y ENVIANDO VIDEO.
    
    Prueba múltiples paths de video conocidos.
    Para cada path:
      1. Conecta al puerto 4747
      2. Envía GET HTTP/1.0
      3. Lee hasta 1024 bytes de respuesta
      4. Verifica que la respuesta contenga:
         - HTTP 200 OK
         - Y uno de:
           a. Marcador JPEG SOI (\xff\xd8) → stream activo
           b. Content-Type multipart/x-mixed-replace → MJPEG header
           c. Content-Type image/jpeg → JPEG directo
    
    Retorna la URL del primer stream de video encontrado, None si no.
    """
    for path in STREAM_PATHS:
        url = DROIDCAM_URL_TEMPLATE.format(ip, DROIDCAM_PORT, path)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SCAN_TIMEOUT + 0.3)  # más tiempo para leer datos
            sock.connect((ip, DROIDCAM_PORT))
            request = f"GET {path} HTTP/1.0\r\n\r\n"
            sock.sendall(request.encode())
            response = sock.recv(4096)
            sock.close()

            # Debe ser HTTP 200
            if not response.startswith(b"HTTP/") or b"200 OK" not in response:
                continue

            # Verificar que sea realmente contenido de video:
            # 1. Marcador JPEG SOI (\xff\xd8) → stream activo con datos
            if b"\xff\xd8" in response:
                return url
            # 2. Content-Type multipart/x-mixed-replace → MJPEG stream header
            if b"multipart/x-mixed-replace" in response:
                return url
            # 3. Content-Type image/jpeg → JPEG directo
            if b"image/jpeg" in response:
                return url

        except Exception:
            continue

    return None


def discover_candidates(timeout: float = 0.15) -> list[str]:
    """
    Escanea la red local para encontrar IPs con el puerto 4747 abierto.
    No verifica el contenido HTTP — solo conecta al puerto.
    Retorna lista de IPs candidatas (ej: ['192.168.1.5', '192.168.1.42']).
    """
    ip_base = get_local_ip_base()
    logger.debug("Buscando IPs candidatas en red %s*...", ip_base)

    ips = [f"{ip_base}{i}" for i in range(1, 255)]
    candidates: list[str] = []

    def _check_port(ip: str) -> str | None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, DROIDCAM_PORT))
            sock.close()
            return ip if result == 0 else None
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(_check_port, ip): ip for ip in ips}
        for future in concurrent.futures.as_completed(futures):
            try:
                ip = future.result()
                if ip:
                    candidates.append(ip)
                    # Cancelar hilos restantes si ya encontramos algunas
                    if len(candidates) >= 5:
                        for f in futures:
                            f.cancel()
                        break
            except Exception:
                pass

    if candidates:
        logger.info("IPs candidatas encontradas: %s", candidates)
    return candidates


def scan_droidcam(
    progress_callback: Callable[[int, int], None] | None = None,
    max_workers: int = 50,
) -> str | None:
    """
    Escanea la red local en busca de DroidCam.
    
    Args:
        progress_callback: Opcional, se llama con (completados, total) por cada IP.
        max_workers: Hilos en paralelo para el escaneo.
        
    Returns:
        URL del stream de video de DroidCam (ej: 'http://192.168.1.5:4747/video')
        o None si no se encontró.
    """
    ip_base = get_local_ip_base()
    logger.info("Escaneando red %s* en busca de DroidCam...", ip_base)

    ips = [f"{ip_base}{i}" for i in range(1, 255)]
    total = len(ips)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_check_ip, ip): ip for ip in ips}
        completed = 0

        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if progress_callback:
                progress_callback(completed, total)

            try:
                url = future.result()
                if url:
                    logger.info("DroidCam encontrado en: %s", url)
                    # Cancelar los hilos restantes
                    for f in futures:
                        f.cancel()
                    return url
            except Exception:
                pass

    logger.info("No se encontró DroidCam en la red %s*", ip_base)
    return None


def scan_droidcam_fast() -> str | None:
    """
    Versión rápida de scan_droidcam sin callback de progreso.
    Usa 254 hilos y timeout de 0.3s por IP → ~1-2 segundos total.
    """
    return scan_droidcam()
