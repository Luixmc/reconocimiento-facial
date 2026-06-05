"""
Script de build para empaquetar el backend Python en un .exe
independiente usando PyInstaller.

Uso:
    cd backend
    python build_backend.py

Esto genera backend/dist/backend.exe
"""
import os
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DIST_DIR = HERE / "dist"
SPEC_NAME = "backend.spec"

# Dependencias ocultas que PyInstaller podría no detectar automáticamente
HIDDEN_IMPORTS = [
    "insightface",
    "insightface.model_zoo",
    "insightface.app",
    # sklearn y skimage no necesarios para insightface buffalo_s
    "onnxruntime",
    "onnx",
]

# Datos adicionales a incluir (modelos, etc.)
DATAS = []


def build():
    """Ejecuta PyInstaller con la configuración adecuada."""
    # Limpiar builds anteriores
    for d in ["build", "dist", "__pycache__"]:
        p = HERE / d
        if p.exists():
            subprocess.run(["rmdir", "/s", "/q", str(p)], shell=True, check=False)

    # Comando PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                    # Un solo .exe
        "--name", "backend",
        "--distpath", str(DIST_DIR),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE),
        "--noconsole",                  # Sin ventana de consola
        "--clean",
    ]

    # Import ocultos
    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])

    # Archivo principal
    cmd.append(str(HERE / "main.py"))

    print("Ejecutando:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    # Verificar resultado
    exe_path = DIST_DIR / "backend.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n[OK] Backend empaquetado: {exe_path} ({size_mb:.1f} MB)")
        return True
    else:
        print(f"\n[ERROR] No se genero {exe_path}")
        return False


if __name__ == "__main__":
    build()
