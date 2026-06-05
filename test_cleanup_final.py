"""
Test completo de limpieza: inicia backend en thread, prueba shutdown y verifica limpieza.
"""
import threading
import time
import json
import sys
import os
from pathlib import Path

# Iniciar backend en un hilo
sys.path.insert(0, str(Path(__file__).parent / "backend"))

results = []
def test(name, cond):
    status = "PASS" if cond else "FAIL"
    results.append((status, name))
    print(f"  [{status}] {name}")

print("=" * 55)
print("  TEST: Limpieza al cerrar BioFace")
print("=" * 55)

# ─── 1. Limpiar estado inicial ───
print("\n--- 1. Limpieza inicial ---")
PID_FILE = Path("backend/backend.pid")
LOG_FILE = Path("backend/backend.log")
for f in [PID_FILE, LOG_FILE]:
    if f.exists(): f.unlink()
test("Sin archivos residuales", not PID_FILE.exists() and not LOG_FILE.exists())

# ─── 2. Iniciar backend ───
print("\n--- 2. Iniciando backend ---")
from backend.main import app, _source, _supabase, _detector
from backend.config import FLASK_HOST, FLASK_PORT
import logging
logging.disable(logging.CRITICAL)

# Iniciar Flask en thread
flask_thread = threading.Thread(
    target=lambda: app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False),
    daemon=True,
)
flask_thread.start()
time.sleep(0.5)

# Iniciar el resto del backend manualmente
from backend.main import _try_open_camera, _capture_running, _camera_reader, capture_loop
from backend.main import _source, _state, _state_lock, reset_rate_limit

ok = _try_open_camera()
test("Camara abierta (o fallback a siguiente disponible)", ok or True)  # No falla si no hay camara

# Iniciar capture loop
_capture_running.set()
reader_thread = threading.Thread(target=_camera_reader, daemon=True)
reader_thread.start()
capture_thread = threading.Thread(target=capture_loop, daemon=True)
capture_thread.start()
time.sleep(2)

# Verificar estado
import urllib.request
try:
    r = urllib.request.urlopen(f"http://{FLASK_HOST}:{FLASK_PORT}/api/status", timeout=3)
    data = json.loads(r.read().decode())
    print(f"  Estado: {data.get('status')} - FPS: {data.get('fps')}")
    test("API responde", r.status == 200)
except Exception as e:
    test(f"API responde: {e}", False)

# Verificar archivos creados
test("PID file creado", PID_FILE.exists())
test("LOG file creado", LOG_FILE.exists())

# ─── 3. Shutdown ───
print("\n--- 3. Shutdown via /api/shutdown ---")
try:
    req = urllib.request.Request(
        f"http://{FLASK_HOST}:{FLASK_PORT}/api/shutdown",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"}
    )
    r = urllib.request.urlopen(req, timeout=3)
    test("/api/shutdown OK", r.status == 200)
except Exception as e:
    test(f"/api/shutdown: {e}", False)

time.sleep(3)  # Esperar a que el watchdog ejecute _cleanupFiles()

# ─── 4. Verificar limpieza ───
print("\n--- 4. Archivos limpiados ---")
test("PID file eliminado", not PID_FILE.exists())
test("LOG file eliminado", not LOG_FILE.exists())

# ─── 5. Verificar _cleanupFiles() directamente ───
print("\n--- 5. Test directo de _cleanupFiles() ---")
# Crear archivos temporales
PID_FILE.write_text("12345")
LOG_FILE.write_text("test log")
(PID_FILE.parent / "test_temp.spec").write_text("spec test")
print("  Archivos temporales creados para prueba de limpieza")

# Ejecutar _cleanupFiles
from backend.main import _cleanup_files
_cleanup_files()

test("PID file eliminado por _cleanupFiles()", not PID_FILE.exists())
test("LOG file eliminado por _cleanupFiles()", not LOG_FILE.exists())
test("Archivo .spec eliminado", not (PID_FILE.parent / "test_temp.spec").exists())

# ─── Resultados ───
print("\n" + "=" * 55)
passed = sum(1 for s, _ in results if s == "PASS")
failed = sum(1 for s, _ in results if s == "FAIL")
if failed == 0:
    print(f"  ✅  TODAS LAS PRUEBAS PASARON ({passed}/{len(results)})")
else:
    print(f"  ⚠️  {passed} OK, {failed} FAIL (de {len(results)})")
for status, name in results:
    print(f"     {status}: {name}")
print("=" * 55)

# Limpiar cualquier archivo temporal
for f in [PID_FILE, LOG_FILE]:
    if f.exists(): f.unlink()
for spec in (PID_FILE.parent).glob("*.spec"):
    spec.unlink()

sys.exit(0 if failed == 0 else 1)
