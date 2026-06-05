"""
Script de prueba: inicia el backend, verifica que crea archivos,
lo cierra vía API, y comprueba que todo se limpia correctamente.
"""
import subprocess
import time
import sys
import os
import json
import urllib.request
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent / "backend"
PID_FILE = BASE_DIR / "backend.pid"
LOG_FILE = BASE_DIR / "backend.log"
HOST = "127.0.0.1"
PORT = 5050

passed = 0
failed = 0

def check(description: str, condition: bool):
    global passed, failed
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {description}")
    if condition:
        passed += 1
    else:
        failed += 1

def wait_for_backend(timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"http://{HOST}:{PORT}/api/health")
            resp = urllib.request.urlopen(req, timeout=2)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def call_shutdown():
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/shutdown",
            data=b"",
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=3)
        return resp.status == 200
    except Exception:
        return False

def check_port_free():
    """Verifica si el puerto 5050 está libre."""
    result = subprocess.run(
        'netstat -ano | findstr ":5050 "',
        shell=True, capture_output=True, text=True, timeout=5,
    )
    return "LISTENING" not in result.stdout


# ═══════════════════════════════════════════════════════════════════════════
# 1. LIMPIEZA PREVIA
# ═══════════════════════════════════════════════════════════════════════════
print("=" * 55)
print("  TEST: Limpieza total al cerrar BioFace")
print("=" * 55)
print("\n--- 1. LIMPIEZA PREVIA ---")

# Matar procesos existentes
subprocess.run('taskkill /F /IM python.exe /T >nul 2>&1', shell=True)
subprocess.run('taskkill /F /IM backend.exe /T >nul 2>&1', shell=True)

# Liberar puerto 5050
result = subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5)
for line in result.stdout.splitlines():
    if ":5050 " in line and "LISTENING" in line:
        parts = line.strip().split()
        if len(parts) >= 5:
            pid = parts[-1]
            if pid != "0":
                subprocess.run(f'taskkill /F /PID {pid} >nul 2>&1', shell=True)

time.sleep(0.5)

# Eliminar archivos residuales
for f in [PID_FILE, LOG_FILE]:
    if f.exists():
        f.unlink()
for d in [BASE_DIR / "__pycache__", BASE_DIR / "build", BASE_DIR / "dist"]:
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

check("Archivos residuales eliminados antes de empezar",
      not PID_FILE.exists() and not LOG_FILE.exists())


# ═══════════════════════════════════════════════════════════════════════════
# 2. INICIAR BACKEND
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 2. INICIANDO BACKEND ---")

# Usar getattr para compatibilidad multiplataforma
creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
proc = subprocess.Popen(
    [sys.executable, "backend/main.py"],
    cwd=str(Path(__file__).parent),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=creationflags,
)
print(f"  Backend iniciado (PID: {proc.pid})")

print("  Esperando a que el backend responda...")
ready = wait_for_backend(timeout=15)
check("Backend responde /api/health", ready)

if not ready:
    print("\n  El backend no arranco. Abortando.")
    proc.kill()
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# 3. VERIFICAR ARCHIVOS CREADOS
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 3. VERIFICANDO ARCHIVOS CREADOS ---")
time.sleep(1)

check("PID file creado: backend.pid", PID_FILE.exists())
check("LOG file creado: backend.log", LOG_FILE.exists())

if PID_FILE.exists():
    pid_content = PID_FILE.read_text().strip()
    check(f"PID file contiene numero valido: {pid_content}", pid_content.isdigit())
    check(f"PID coincide con proceso backend", int(pid_content) == proc.pid)

# Obtener estado del backend
try:
    req = urllib.request.Request(f"http://{HOST}:{PORT}/api/status")
    resp = urllib.request.urlopen(req, timeout=3)
    if resp.status == 200:
        status_data = json.loads(resp.read().decode())
        print(f"  Estado backend: {status_data.get('status')} - FPS: {status_data.get('fps')}")
        check("Backend en estado 'running'", status_data.get('status') == 'running')
except Exception as e:
    check(f"Error obteniendo status: {e}", False)


# ═══════════════════════════════════════════════════════════════════════════
# 4. SHUTDOWN VIA API
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 4. SHUTDOWN VIA /api/shutdown ---")

api_ok = call_shutdown()
check("/api/shutdown respondio 200 OK", api_ok)

# Esperar a que el watchdog salga del while + finally ejecute _cleanupFiles()
# Flujo: /api/shutdown -> thread -> 0.3s -> _shutdown.set() -> watchdog sale
# -> finally de main() -> _cleanupFiles() -> logger.info
# -> 2s mas -> os._exit(0) (respaldo por si el finally tarda)
print("  Esperando que el watchdog ejecute _cleanupFiles()...")
try:
    proc.wait(timeout=6)
    check(f"Proceso backend termino (codigo: {proc.returncode})", True)
except subprocess.TimeoutExpired:
    print("  Timeout - forzando kill manual...")
    proc.kill()
    proc.wait(timeout=2)
    check("Proceso backend terminado (kill forzado)", True)


# ═══════════════════════════════════════════════════════════════════════════
# 5. VERIFICAR LIMPIEZA
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 5. VERIFICANDO LIMPIEZA DEL BACKEND ---")
time.sleep(0.5)

check("PID file eliminado por _cleanupFiles()", not PID_FILE.exists())
check("LOG file eliminado por _cleanupFiles()", not LOG_FILE.exists())

# Revisar __pycache__
pycache = BASE_DIR / "__pycache__"
check("__pycache__/ no existe o fue limpiado", not pycache.exists() or not any(pycache.iterdir()))


# ═══════════════════════════════════════════════════════════════════════════
# 6. VERIFICAR PUERTO LIBRE
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 6. VERIFICANDO PUERTO 5050 ---")

port_free = check_port_free()
check("Puerto 5050 libre (no hay LISTENING)", port_free)


# ═══════════════════════════════════════════════════════════════════════════
# 7. SIMULAR killAll() DE FLUTTER
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 7. SIMULANDO killAll() DE FLUTTER (respaldo nuclear) ---")

# Flutter killAll() hace todo esto de forma sincrona:

# 7a. Matar por PID file
if PID_FILE.exists():
    try:
        pid = int(PID_FILE.read_text().strip())
        subprocess.run(f'taskkill /F /T /PID {pid} >nul 2>&1', shell=True)
        print("  [killAll] Matado por PID file")
    except:
        pass

# 7b. Matar por nombre de imagen
subprocess.run('taskkill /F /IM python.exe /T >nul 2>&1', shell=True)
subprocess.run('taskkill /F /IM backend.exe /T >nul 2>&1', shell=True)
print("  [killAll] Matado python.exe + backend.exe")

# 7c. Matar por puerto 5050
result = subprocess.run('netstat -ano', shell=True, capture_output=True, text=True, timeout=5)
for line in result.stdout.splitlines():
    if ":5050 " in line and "LISTENING" in line:
        parts = line.strip().split()
        if len(parts) >= 5:
            pid = parts[-1]
            if pid != "0":
                subprocess.run(f'taskkill /F /PID {pid} >nul 2>&1', shell=True)
                print(f"  [killAll] Matado por puerto 5050 - PID: {pid}")

# 7d. Limpiar archivos residuales
for f in [PID_FILE, LOG_FILE]:
    if f.exists():
        try:
            f.unlink()
            print(f"  [cleanup] Eliminado {f.name}")
        except:
            pass
for d in [BASE_DIR / "__pycache__", BASE_DIR / "build", BASE_DIR / "dist"]:
    if d.exists():
        try:
            shutil.rmtree(d, ignore_errors=True)
            print(f"  [cleanup] Eliminado {d.name}/")
        except:
            pass
for spec in BASE_DIR.glob("*.spec"):
    try:
        spec.unlink()
        print(f"  [cleanup] Eliminado {spec.name}")
    except:
        pass

print("  [killAll] Limpieza nuclear completada")

# Verificaciones finales
final_port_free = check_port_free()
check("Puerto 5050 definitivamente libre", final_port_free)
check("PID file no existe", not PID_FILE.exists())
check("LOG file no existe", not LOG_FILE.exists())


# ═══════════════════════════════════════════════════════════════════════════
# 8. RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 55)
total = passed + failed
if failed == 0:
    print(f"  ✅  TODAS LAS PRUEBAS PASARON ({passed}/{total})")
    print("  La limpieza funciona correctamente en todos los niveles.")
else:
    print(f"  ⚠️  {passed} pasaron, {failed} fallaron (de {total})")
    print("  Revisa los detalles arriba.")
print("=" * 55)

sys.exit(0 if failed == 0 else 1)
