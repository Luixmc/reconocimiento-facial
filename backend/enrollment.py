"""
Enrollment facial: captura rostros, extrae embeddings y los registra en
Supabase para que el sistema de reconocimiento pueda identificar a la persona.

Uso:
    python backend/enrollment.py                        # cámara por defecto
    python backend/enrollment.py --camera 1             # cámara específica
    python backend/enrollment.py --samples 10           # 10 muestras

Flujo:
    1. Lista personas registradas en Supabase (registered_persons)
    2. Verifica si la persona ya tiene embeddings registrados
    3. Seleccionas quién se va a enrolar
    4. La cámara se abre y captura N muestras faciales
    5. Sube snapshots + recortes faciales a Supabase Storage
    6. Registra embeddings en face_embeddings
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Asegurar que backend/ esta en el path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

try:
    import cv2
except ImportError:
    print("ERROR: OpenCV no instalado. Ejecuta: pip install opencv-python")
    sys.exit(1)

import numpy as np
import requests

from config import (
    COMPANY_ID,
    DEFAULT_CAMERA_INDEX,
    MIN_CONFIDENCE_TO_CAPTURE,
    MODEL_NAME,
    MODEL_VERSION,
    SERVICE_KEY,
    STORAGE_BUCKET,
    SUPABASE_URL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("enrollment")

# ── Intentar cargar InsightFace ──────────────────────────────────────────
try:
    from insightface.app import FaceAnalysis
    HAS_INSIGHTFACE = True
except ImportError:
    HAS_INSIGHTFACE = False
    logger.error("InsightFace no instalado. Ejecuta: pip install insightface")
    sys.exit(1)

# ── Intentar cargar CameraManager para listar cámaras ─────────────────────
try:
    from camera_manager import CameraManager
    HAS_CAMERA_MANAGER = True
except ImportError:
    HAS_CAMERA_MANAGER = False


# ═══════════════════════════════════════════════════════════════════════════
# Cliente Supabase (mnimo, solo lo que necesita enrollment)
# ═══════════════════════════════════════════════════════════════════════════

class SupabaseClient:
    def __init__(self) -> None:
        self._headers = {
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
        }
        self._base_url = SUPABASE_URL.rstrip("/")

    def fetch_persons(self) -> list[dict]:
        """Obtiene todas las personas activas de la empresa."""
        url = f"{self._base_url}/rest/v1/registered_persons"
        params = {
            "select": "id,full_name,document_number,position",
            "company_id": f"eq.{COMPANY_ID}",
            "status": "eq.active",
            "order": "full_name.asc",
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.ok:
            return resp.json()
        logger.error("Error obteniendo personas: %s", resp.text)
        return []

    def count_embeddings(self, person_id: str) -> int:
        """Cuenta cuantos embeddings activos tiene una persona."""
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

    def upload_snapshot(self, image_bytes: bytes, prefix: str) -> str | None:
        """Sube una foto a Supabase Storage."""
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.jpg"
        url = f"{self._base_url}/storage/v1/object/{STORAGE_BUCKET}/{filename}"
        headers = {
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "image/jpeg",
        }
        resp = requests.post(url, headers=headers, data=image_bytes, timeout=15)
        if resp.ok:
            return f"{self._base_url}/storage/v1/object/public/{STORAGE_BUCKET}/{filename}"
        logger.warning("Error subiendo snapshot: %s", resp.text)
        return None

    def insert_embedding(
        self,
        person_id: str,
        embedding: list[float],
        is_primary: bool = False,
    ) -> bool:
        """Inserta un embedding facial en face_embeddings."""
        embedding_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": embedding_id,
            "company_id": COMPANY_ID,
            "person_id": person_id,
            "embedding": embedding,
            "embedding_dimension": len(embedding),
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "source": "enrollment",
            "status": "active",
            "is_primary": is_primary,
            "created_at": now,
            "updated_at": now,
        }
        url = f"{self._base_url}/rest/v1/face_embeddings"
        resp = requests.post(
            url,
            headers={**self._headers, "Prefer": "return=minimal"},
            json=payload,
            timeout=10,
        )
        if resp.ok:
            logger.info("   Embedding registrado: %s", embedding_id)
            return True
        logger.error("   Error insertando embedding: %s", resp.text)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Inicializacin de InsightFace
# ═══════════════════════════════════════════════════════════════════════════

def init_face_analysis() -> FaceAnalysis:
    """Inicializa el modelo de InsightFace."""
    logger.info("Inicializando InsightFace (buffalo_l)...")
    app = FaceAnalysis(
        name="buffalo_l",
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_thresh=0.5)
    logger.info("Modelo listo.")
    return app


# ═══════════════════════════════════════════════════════════════════════════
# UI en terminal
# ═══════════════════════════════════════════════════════════════════════════

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_header():
    clear_screen()
    print("=" * 55)
    print("   BioFace - Enrollment Facial")
    print("   Registro de embeddings faciales en Supabase")
    print("=" * 55)
    print()


def select_person(persons: list[dict]) -> dict | None:
    """Muestra un menu numerado para elegir una persona."""
    if not persons:
        print("No hay personas registradas en Supabase.")
        print("Crea primero registros en la tabla 'registered_persons'.")
        return None

    print("Personas disponibles para enrollment:\n")
    for i, p in enumerate(persons, 1):
        print(
            f"  [{i}] {p['full_name']:20s} | "
            f"{p.get('document_number', 'N/A'):12s} | "
            f"{p.get('position', 'N/A')}"
        )
    print("  [0] Cancelar")

    while True:
        try:
            choice = input("\nSelecciona una persona (numero): ").strip()
            if choice == "0":
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(persons):
                return persons[idx]
            print("  Numero invalido. Intenta de nuevo.")
        except ValueError:
            print("  Ingresa un numero valido.")


def warn_existing_embeddings(supabase: SupabaseClient, person: dict) -> bool:
    """Advise si la persona ya tiene embeddings y pide confirmacion."""
    count = supabase.count_embeddings(person["id"])
    if count > 0:
        print(f"\n  [!] {person['full_name']} YA TIENE {count} embedding(s) registrado(s).")
        print("  Si continuas, se agregaran MAS embeddings (los anteriores no se borran).")
        resp = input("  Continuar de todas formas? (s/N): ").strip().lower()
        return resp in ("s", "si", "y", "yes")
    return True


def confirm_enrollment(person: dict, camera_index: int, num_samples: int) -> bool:
    """Pide confirmacion antes de empezar."""
    print(f"\n  Vas a enrolar a: {person['full_name']}")
    print(f"  Documento: {person.get('document_number', 'N/A')}")
    print(f"  Cargo:     {person.get('position', 'N/A')}")
    print(f"  Camara:    {camera_index}")
    print(f"  Muestras:  {num_samples}")
    print()
    print("  Asegurate de tener buena iluminacion.")
    print("  Mira fijamente a la camara.")
    print()
    resp = input("  Comenzar? (s/N): ").strip().lower()
    return resp in ("s", "si", "y", "yes")


# ═══════════════════════════════════════════════════════════════════════════
# Captura de muestras
# ═══════════════════════════════════════════════════════════════════════════

def capture_samples(
    app: FaceAnalysis,
    person_name: str,
    camera_index: int = 0,
    num_samples: int = 5,
) -> list[dict]:
    """
    Captura N muestras faciales con la camara.
    Retorna lista de dicts con: embedding, snapshot_bytes, face_bytes.
    """
    print(f"\n  Abriendo camara {camera_index}...")
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        logger.error("No se pudo abrir la camara %d", camera_index)
        return []

    samples: list[dict] = []
    captured = 0
    attempts = 0
    max_attempts = num_samples * 10

    print(f"\n  Necesitamos {num_samples} capturas validas.")
    print("  La captura es automatica al detectar un rostro.")
    print("  Presiona ESPACIO para captura manual.")
    print("  Presiona ESC para cancelar.\n")

    window_name = f"Enrollment - {person_name}"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 640, 480)

    while captured < num_samples and attempts < max_attempts:
        ret, frame = cap.read()
        if not ret:
            continue

        attempts += 1
        display = frame.copy()
        faces = app.get(frame)

        # Dibujar rectangulos
        for face in faces:
            bbox = face.bbox.astype(int)
            cv2.rectangle(
                display,
                (bbox[0], bbox[1]),
                (bbox[2], bbox[3]),
                (0, 255, 0), 2,
            )
            cv2.putText(
                display,
                f"Conf: {face.det_score:.2f}",
                (bbox[0], bbox[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 255, 0), 2,
            )

        # Info en pantalla
        cv2.putText(
            display,
            f"Capturas: {captured}/{num_samples}  Intentos: {attempts}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
        )
        if faces:
            cv2.putText(
                display,
                "ROSTRO DETECTADO",
                (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )

        cv2.imshow(window_name, display)

        # Auto-capturar
        if len(faces) == 1 and faces[0].det_score > MIN_CONFIDENCE_TO_CAPTURE:
            face = faces[0]
            embedding = face.embedding.tolist()

            _, snap_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            snapshot_bytes = snap_buf.tobytes()

            bbox = face.bbox.astype(int)
            x1 = max(0, bbox[0])
            y1 = max(0, bbox[1])
            x2 = min(frame.shape[1], bbox[2])
            y2 = min(frame.shape[0], bbox[3])
            face_img = frame[y1:y2, x1:x2]
            if face_img.size > 0:
                _, face_buf = cv2.imencode(".jpg", face_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
                face_bytes = face_buf.tobytes()
            else:
                face_bytes = snapshot_bytes

            samples.append({
                "embedding": embedding,
                "snapshot_bytes": snapshot_bytes,
                "face_bytes": face_bytes,
                "confidence": float(face.det_score),
            })
            captured += 1

            cv2.putText(
                display,
                f"Captura {captured}/{num_samples}",
                (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 3,
            )
            cv2.imshow(window_name, display)
            cv2.waitKey(500)

        key = cv2.waitKey(30) & 0xFF
        if key == 27:  # ESC
            print("\n  Cancelado por el usuario.")
            break
        if key == 32 and len(faces) == 1:  # SPACE
            face = faces[0]
            embedding = face.embedding.tolist()
            _, snap_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            samples.append({
                "embedding": embedding,
                "snapshot_bytes": snap_buf.tobytes(),
                "face_bytes": snap_buf.tobytes(),
                "confidence": float(face.det_score),
            })
            captured += 1

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n  Capturas obtenidas: {len(samples)}/{num_samples}")
    return samples


# ═══════════════════════════════════════════════════════════════════════════
# Subida a Supabase
# ═══════════════════════════════════════════════════════════════════════════

def upload_samples(
    supabase: SupabaseClient,
    person: dict,
    samples: list[dict],
) -> int:
    """
    Sube las muestras a Supabase.
    - Sube snapshot + recorte facial a Storage
    - Registra embedding en face_embeddings
    Retorna cuantas se subieron exitosamente.
    """
    if not samples:
        return 0

    person_name = person["full_name"]
    person_id = person["id"]
    uploaded = 0

    prefix = person_name.lower().replace(" ", "_").replace("ñ", "n")

    print(f"\n  Subiendo {len(samples)} muestras a Supabase...\n")

    for i, sample in enumerate(samples, 1):
        print(f"  [{i}/{len(samples)}] Procesando...")

        # 1. Subir snapshot (frame completo)
        snap_url = supabase.upload_snapshot(
            sample["snapshot_bytes"],
            f"enrollment_{prefix}",
        )

        # 2. Subir recorte facial
        face_url = supabase.upload_snapshot(
            sample["face_bytes"],
            f"face_{prefix}",
        )

        if snap_url:
            print(f"     Snapshot: {snap_url}")
        if face_url:
            print(f"     Rostro:   {face_url}")

        # 3. Registrar embedding (aunque las imagenes fallen, el embedding es lo importante)
        is_primary = i == 1
        ok = supabase.insert_embedding(
            person_id=person_id,
            embedding=sample["embedding"],
            is_primary=is_primary,
        )
        if ok:
            uploaded += 1

    return uploaded


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="BioFace - Enrollment Facial",
    )
    parser.add_argument(
        "--camera", type=int, default=DEFAULT_CAMERA_INDEX,
        help=f"Indice de la camara (default: {DEFAULT_CAMERA_INDEX})",
    )
    parser.add_argument(
        "--samples", type=int, default=5,
        help="Numero de muestras a capturar (default: 5, minimo: 3)",
    )
    parser.add_argument(
        "--list-cameras", action="store_true",
        help="Lista las camaras disponibles y sale",
    )
    args = parser.parse_args()

    # Asegurar muestras minimas
    num_samples = max(3, args.samples)

    # Listar camaras si se pide
    if args.list_cameras:
        if HAS_CAMERA_MANAGER:
            cameras = CameraManager.list_cameras()
        else:
            cameras = []
            for idx in range(5):
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        cameras.append({"index": idx, "name": f"Camara {idx}"})
                    cap.release()
        if cameras:
            print("Camaras disponibles:")
            for c in cameras:
                print(f"  [{c['index']}] {c['name']}")
        else:
            print("No se encontraron camaras.")
        sys.exit(0)

    print_header()

    # 1. Conectar a Supabase
    logger.info("Conectando a Supabase...")
    supabase = SupabaseClient()

    # 2. Listar personas
    persons = supabase.fetch_persons()
    if not persons:
        print("No hay personas registradas en la empresa.")
        print("Ve al panel de administracion y crea registros en 'registered_persons'.")
        sys.exit(1)

    # 3. Elegir persona
    person = select_person(persons)
    if person is None:
        print("\nCancelado.")
        sys.exit(0)

    # 4. Advertir si ya tiene embeddings
    if not warn_existing_embeddings(supabase, person):
        print("\nCancelado.")
        sys.exit(0)

    # 5. Confirmar
    if not confirm_enrollment(person, args.camera, num_samples):
        print("\nCancelado.")
        sys.exit(0)

    # 6. Inicializar InsightFace
    print("\n  Cargando modelo de reconocimiento facial...")
    app = init_face_analysis()

    # 7. Capturar muestras
    samples = capture_samples(app, person["full_name"], args.camera, num_samples)

    if len(samples) < 3:
        print("\nNo se obtuvieron suficientes muestras validas (minimo 3).")
        print("Asegurate de tener buena iluminacion y mira directamente a la camara.")
        sys.exit(1)

    # 8. Subir a Supabase
    uploaded = upload_samples(supabase, person, samples)

    # 9. Notificar al backend para recargar embeddings sin reiniciar
    if uploaded > 0:
        try:
            from config import FLASK_PORT
            resp = requests.post(
                f"http://127.0.0.1:{FLASK_PORT}/api/refresh-embeddings",
                timeout=3,
            )
            if resp.ok:
                print("  [OK] Backend recargó embeddings automáticamente.")
            else:
                print("  [!] Backend no pudo recargar (reinicia manualmente).")
        except Exception:
            print("  [!] Backend no está corriendo — reinícialo para activar los cambios.")

    # 10. Resumen
    print()
    print("=" * 55)
    print(f"  Enrollment de '{person['full_name']}' finalizado.")
    print(f"  Muestras capturadas: {len(samples)}")
    print(f"  Embeddings registrados: {uploaded}")
    print(f"  Estado: {'Completado' if uploaded > 0 else 'Fallido'}")
    print("=" * 55)
    print()
    print("  Ahora el sistema de reconocimiento facial podra")
    print("  identificar a esta persona automaticamente.")
    print()


if __name__ == "__main__":
    main()
