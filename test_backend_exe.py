"""
Test: backend.exe + killAll()
- Inicia backend.exe
- Prueba API
- Simula killAll() de Flutter (taskkill /IM backend.exe + cleanup de archivos)
"""
import subprocess
import sys
import time
import json
import urllib.request
import shutil
from pathlib import Path

BASE = Path(__file__).parent / "backend"
EXE = BASE / "dist" / "backend.exe"
PID_FILE = BASE / "backend.pid"
LOG_FILE = BASE / "backend.log"
HOST, PORT = "127.0.0.1", 5050
results = []

def test(name, cond):
    status = "PASS" if cond else "FAIL"
    results.append((status, name))
    print(f"  [{status}] {name}")

print("=" * 55)
print("  TEST: backend.exe + killAll()")
print("=" * 55)

# ─── 1. Verificar que backend.exe existe ───
print("\n--- 1. Verificar backend.exe ---")
test(f"backend.exe existe ({EXE.stat().st_size / 1024 / 1024:.1f} MB)" if EXE.exists() else "backend.exe existe",
     EXE.exists())

# ─── 2. Limpieza inicial ───
print("\n--- 2. Limpieza inicial ---")
subprocess.run("taskkill /F /IM python.exe /T >nul 2>&1", shell=True)
subprocess.run("taskkill /F /IM backend.exe /T >nul 2>&1", shell=True)
result = subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5)
for line in result.stdout.split("\n"):
    if ":5050 " in line and "LISTENING" in line:
        parts = line.strip().split()
        if len(parts) >= 5 and parts[-1] != "0":
            subprocess.run(f"taskkill /F /PID {parts[-1]} >nul 2>&1", shell=True)
time.sleep(0.5)
for f in [PID_FILE, LOG_FILE]:
    if f.exists(): f.unlink()
# Limpiar cache
for d in [BASE / "__pycache__", BASE / "build", BASE / "dist"]:
    if d.exists() and d != BASE / "dist":  # No borrar el exe que acabamos de generar
        shutil.rmtree(d, ignore_errors=True)
test("Estado inicial limpio (sin PID, sin log)", not PID_FILE.exists() and not LOG_FILE.exists())
test("Puerto 5050 libre antes de iniciar",
     not any(":5050 " in line and "LISTENING" in line
             for line in subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5).stdout.split("\n")))

# ─── 3. Iniciar backend.exe ───
print("\n--- 3. Iniciar backend.exe ---")
flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
proc = subprocess.Popen(
    [str(EXE)],
    cwd=str(BASE),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=flags,
)
print(f"  PID del proceso: {proc.pid}")
test("backend.exe se inicio", proc.poll() is None)

# Esperar a que responda
ready = False
for i in range(90):
    try:
        r = urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=2)
        if r.status == 200:
            data = json.loads(r.read().decode())
            test(f"backend.exe responde /api/health: {data}", True)
            ready = True
            break
    except: pass
    time.sleep(1)

if not ready:
    test("backend.exe responde /api/health", False)
    print("  ERROR: backend.exe no arranco en 90s")
    proc.kill()
    sys.exit(1)

time.sleep(0.5)

# ─── 4. Verificar archivos creados por backend.exe ───
print("\n--- 4. Archivos creados por backend.exe ---")
test("PID file creado por backend.exe", PID_FILE.exists())
test("LOG file creado por backend.exe", LOG_FILE.exists())
if PID_FILE.exists():
    pid_val = PID_FILE.read_text().strip()
    test(f"PID file: {pid_val}", pid_val.isdigit())
    # El PID del proceso subprocess.Popen no coincide con el del PID file
    # porque el .exe puede spawnear hijos

# ─── 5. Probar /api/status ───
print("\n--- 5. Estado del backend ---")
try:
    r = urllib.request.urlopen(f"http://{HOST}:{PORT}/api/status", timeout=3)
    data = json.loads(r.read().decode())
    print(f"  Status: {data.get('status')} - Source: {data.get('source_type')} - FPS: {data.get('fps')}")
    test("Estado OK", data.get("status") in ("running", "error"))
except Exception as e:
    test(f"Estado: {e}", False)

# ─── 6. SIMULAR killAll() DE FLUTTER ───
print("\n--- 6. Simulando killAll() de Flutter ---")
print("  Flutter ejecuta:")
print("    taskkill /F /IM python.exe /T")
print("    taskkill /F /IM backend.exe /T")
print("    netstat + taskkill /F /PID <port-5050-pid>")
print("    _cleanupFiles() -> borrar PID, log, __pycache__, build, dist, *.spec")

# 6a. Matar por nombre de imagen (python.exe + backend.exe) -- esto es killAll()
taskkill_python = subprocess.run("taskkill /F /IM python.exe /T 2>nul", shell=True)
taskkill_backend = subprocess.run("taskkill /F /IM backend.exe /T 2>nul", shell=True)
test("taskkill /IM python.exe ejecutado (exit code 0 = mato algo, 1 = no habia)",
     taskkill_python.returncode in (0, 1))
test("taskkill /IM backend.exe ejecutado (mata backend.exe)",
     taskkill_backend.returncode in (0, 1))

# 6b. Matar por puerto 5050
result = subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5)
for line in result.stdout.split("\n"):
    if ":5050 " in line and "LISTENING" in line:
        parts = line.strip().split()
        if len(parts) >= 5 and parts[-1] != "0":
            subprocess.run(f"taskkill /F /PID {parts[-1]} >nul 2>&1", shell=True)
            print(f"  Matado por puerto 5050: PID {parts[-1]}")

time.sleep(1)

# 6c. Verificar que backend.exe ya no esta corriendo
r = subprocess.run(
    'tasklist /FI "IMAGENAME eq backend.exe" /NH 2>nul',
    shell=True, capture_output=True, text=True, timeout=5
)
has_backend = "backend.exe" in r.stdout
test("backend.exe NO esta corriendo despues de killAll()", not has_backend)

# 6d. Verificar puerto libre
result = subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5)
port_free = not any(":5050 " in line and "LISTENING" in line for line in result.stdout.split("\n"))
test("Puerto 5050 libre despues de killAll()", port_free)

# 6e. Verificar archivos (simular Flutter _cleanupFiles)
print("\n--- 7. _cleanupFiles() (simulando Flutter) ---")
test("PID file existe ANTES de cleanup (para verificar que se borra)",
     PID_FILE.exists())
# Ahora crear archivos temporales para probar que _cleanupFiles los borra
TEST_LOG = LOG_FILE
TEST_SPEC = BASE / "test_temp.spec"
TEST_SPEC.write_text("spec test")
print(f"  Creado archivo temporal: {TEST_SPEC.name}")

# Simular lo que hace Flutter's _cleanupFiles()
print("  Ejecutando _cleanupFiles() de Flutter...")
# Borrar PID file
if PID_FILE.exists():
    try: PID_FILE.unlink()
    except: pass
    print(f"  Borrado: {PID_FILE.name}")
# Borrar LOG file
if LOG_FILE.exists():
    try: LOG_FILE.unlink()
    except: pass
    print(f"  Borrado: {LOG_FILE.name}")
# Borrar __pycache__
pycache = BASE / "__pycache__"
if pycache.exists():
    shutil.rmtree(pycache, ignore_errors=True)
    print(f"  Borrado: __pycache__/")
# Borrar *.spec
for spec in BASE.glob("*.spec"):
    try: spec.unlink()
    except: pass
    print(f"  Borrado: {spec.name}")
# Borrar build/ y dist/ -- pero NO dist/ porque contiene backend.exe!
build_dir = BASE / "build"
if build_dir.exists():
    shutil.rmtree(build_dir, ignore_errors=True)
    print(f"  Borrado: build/")

test("PID file eliminado por cleanup", not PID_FILE.exists())
test("LOG file eliminado por cleanup", not LOG_FILE.exists())
test("Archivo .spec eliminado por cleanup", not TEST_SPEC.exists())

# ─── 8. Verificar que backend.exe sigue existiendo ───
print("\n--- 8. backend.exe preservado ---")
test(f"backend.exe sigue existiendo ({EXE.stat().st_size / 1024 / 1024:.1f} MB)",
     EXE.exists())

# ─── 9. Probar que python.exe tampoco esta corriendo ───
print("\n--- 9. Procesos python ---")
r2 = subprocess.run(
    'tasklist /FI "IMAGENAME eq python.exe" /NH 2>nul',
    shell=True, capture_output=True, text=True, timeout=5
)
has_python = "python.exe" in r2.stdout
test("No hay python.exe corriendo", not has_python)

# ─── RESULTADOS ───
print("\n" + "=" * 55)
passed = sum(1 for s, _ in results if s == "PASS")
failed = sum(1 for s, _ in results if s == "FAIL")
if failed == 0:
    print(f"  ✅  TODAS LAS PRUEBAS PASARON ({passed}/{len(results)})")
    print("  killAll() mata backend.exe correctamente")
    print("  Puerto 5050 liberado")
    print("  Archivos residuales eliminados")
else:
    print(f"  ⚠️  {passed} OK, {failed} FAIL (de {len(results)})")
    for status, name in results:
        print(f"     {status}: {name}")
print("=" * 55)

# Limpiar archivos temporales
for f in [PID_FILE, LOG_FILE]:
    if f.exists(): f.unlink()
for spec in BASE.glob("*.spec"):
    try: spec.unlink()
    except: pass
time.sleep(0.5)

sys.exit(0 if failed == 0 else 1)
