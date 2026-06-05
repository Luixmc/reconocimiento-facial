"""
Script simple de prueba: inicia backend, verifica archivos, lo cierra, verifica limpieza.
"""
import subprocess
import sys
import time
import json
import urllib.request
import shutil
from pathlib import Path

BASE = Path(__file__).parent / "backend"
PID_FILE = BASE / "backend.pid"
LOG_FILE = BASE / "backend.log"
HOST, PORT = "127.0.0.1", 5050

results = []

def test(name, condition):
    status = "PASS" if condition else "FAIL"
    results.append((status, name))
    print(f"  [{status}] {name}")

print("=" * 55)
print("  TEST: Limpieza al cerrar BioFace")
print("=" * 55)

# Paso 1: Limpiar estado inicial
print("\n--- 1. Limpieza inicial ---")
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
test("Estado inicial limpio", not PID_FILE.exists() and not LOG_FILE.exists())

# Paso 2: Iniciar backend
print("\n--- 2. Iniciar backend ---")
flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
proc = subprocess.Popen(
    [sys.executable, "backend/main.py"],
    cwd=str(Path(__file__).parent),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    creationflags=flags,
)
print(f"  PID del proceso: {proc.pid}")

ready = False
for i in range(30):
    try:
        r = urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=2)
        if r.status == 200:
            ready = True
            break
    except: pass
    time.sleep(0.5)
test("Backend responde /api/health", ready)

if not ready:
    print("\n  ERROR: Backend no arranco, abortando")
    proc.kill()
    sys.exit(1)

time.sleep(0.5)

# Paso 3: Verificar archivos creados
print("\n--- 3. Archivos creados ---")
test("PID file creado", PID_FILE.exists())
test("LOG file creado", LOG_FILE.exists())
if PID_FILE.exists():
    pid_val = PID_FILE.read_text().strip()
    test(f"PID file contiene numero: {pid_val}", pid_val.isdigit())
    test(f"PID coincide ({pid_val} == {proc.pid})", int(pid_val) == proc.pid)

# Verificar status del backend
try:
    r = urllib.request.urlopen(f"http://{HOST}:{PORT}/api/status", timeout=3)
    data = json.loads(r.read().decode())
    test(f"Estado: {data.get('status')}", data.get("status") == "running")
except Exception as e:
    test(f"Error obteniendo status: {e}", False)

# Paso 4: Shutdown via API
print("\n--- 4. Shutdown via /api/shutdown ---")
try:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/shutdown", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"}
    )
    r = urllib.request.urlopen(req, timeout=3)
    test("/api/shutdown respondio 200 OK", r.status == 200)
except Exception as e:
    test(f"/api/shutdown error: {e}", False)

# Esperar que el watchdog ejecute finally -> _cleanupFiles()
print("  Esperando cierre del proceso...")
try:
    proc.wait(timeout=6)
    print(f"  Proceso termino (codigo: {proc.returncode})")
except subprocess.TimeoutExpired:
    print("  Timeout - forzando kill")
    proc.kill()
    proc.wait(timeout=2)
time.sleep(0.5)

# Paso 5: Verificar limpieza de archivos
print("\n--- 5. Archivos limpiados ---")
test("PID file eliminado", not PID_FILE.exists())
test("LOG file eliminado", not LOG_FILE.exists())

# Paso 6: Verificar puerto libre
print("\n--- 6. Puerto 5050 ---")
result = subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5)
port_ok = not any(":5050 " in line and "LISTENING" in line for line in result.stdout.split("\n"))
test("Puerto 5050 libre", port_ok)

# Paso 7: Procesos python
print("\n--- 7. Procesos Python ---")
r = subprocess.run(
    'tasklist /FI "IMAGENAME eq python.exe" /NH 2>nul',
    shell=True, capture_output=True, text=True, timeout=5
)
has_python = "python.exe" in r.stdout
test("No hay python.exe corriendo", not has_python)

# Paso 8: Resumen
print("\n" + "=" * 55)
passed = sum(1 for s, _ in results if s == "PASS")
failed = sum(1 for s, _ in results if s == "FAIL")
if failed == 0:
    print(f"  ✅  TODAS LAS PRUEBAS PASARON ({passed}/{len(results)})")
    print("  La limpieza funciona correctamente en todos los niveles:")
else:
    print(f"  ⚠️  {passed} OK, {failed} FAIL (de {len(results)})")
for status, name in results:
    print(f"     {status}: {name}")
print("=" * 55)
sys.exit(0 if failed == 0 else 1)
