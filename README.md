# BioFace - Sistema de Reconocimiento Facial

> **Módulo de captura facial para empleados** — parte del ecosistema BioFace.
>
> Este componente corre directamente en los puestos de entrada de los empleados.
> No requiere login. Solo cámara. Detecta el rostro, identifica a la persona,
> registra entrada/salida y envía la foto a Supabase para que el dashboard
> la visualice en tiempo real.
>
> **Arquitectura headless** — Sin ventanas flotantes de OpenCV.
> Todo se ve en la UI de Flutter, incluido el video en vivo de la cámara.

---

## 📋 Tabla de Contenido

- [Arquitectura](#arquitectura)
- [Flujo de operación](#flujo-de-operacion)
- [Stack tecnológico](#stack-tecnologico)
- [Requisitos](#requisitos)
- [Instalación y ejecución](#instalacion-y-ejecucion)
- [Video en vivo en Flutter](#video-en-vivo-en-flutter)
- [Selector de cámara](#selector-de-camara)
- [Detección de cámaras virtuales (DroidCam, OBS)](#deteccion-de-camaras-virtuales-droidcam-obs)
- [Enrollment facial](#enrollment-facial)
- [Cierre automático de procesos](#cierre-automatico-de-procesos)
- [API HTTP (para Flutter)](#api-http-para-flutter)
- [Rate limiting y anti-spam](#rate-limiting-y-anti-spam)
- [Base de datos (Supabase)](#base-de-datos-supabase)
- [Roadmap SaaS](#roadmap-saas)
- [Licencia](#licencia)

---

## 🏗️ Arquitectura

```
┌──────────────────────────────────────────────────┐
│               PUESTO DEL EMPLEADO                 │
│                                                   │
│  ┌──────────────┐   HTTP (5050)   ┌────────────┐ │
│  │   Flutter     │ ◄────────────► │   Python    │ │
│  │   (UI única)  │                │  (Backend   │ │
│  │               │                │   headless) │ │
│  │ · Video en    │                │             │ │
│  │   vivo desde  │  /api/snapshot │ · OpenCV    │ │
│  │   la cámara   │ ◄──── JSON ─── │ · Insight   │ │
│  │ · Selector de │                │   Face      │ │
│  │   cámara      │                │ · Flask API │ │
│  │ · Estado en   │                │ · Rate Lim. │ │
│  │   tiempo real │                │ · Attend.   │ │
│  └──────────────┘                └──────┬──────┘ │
│  · Sin ventanas OpenCV                  │        │
│  · Al cerrar Flutter →                  │        │
│    backend muere solo                   │        │
└─────────────────────────────────────────┼────────┘
                                          │
                                   ┌──────▼───────┐
                                   │   Supabase    │
                                   │               │
                                   │ · Storage     │
                                   │   (fotos)     │
                                   │ · PostgreSQL  │
                                   │   (registros) │
                                   └──────┬───────┘
                                          │
                                   ┌──────▼───────┐
                                   │   Dashboard   │
                                   │  (otro módulo)│
                                   └──────────────┘
```

### Características clave de la arquitectura

| Aspecto | Descripción |
|---------|-------------|
| **Headless** | El backend Python NO abre ventanas OpenCV. Todo se ve en Flutter |
| **Video en vivo** | Flutter poll `/api/snapshot` cada 200ms → `Image.memory()` |
| **Auto-inicio** | Flutter detecta Python y arranca el backend automáticamente |
| **Auto-cierre** | Al cerrar Flutter, el backend se mata por PID, nombre y puerto |
| **Sin dependencias UI** | No requiere monitor para el backend — corre en segundo plano |

---

## 🔄 Flujo de operación

1. **Inicio**: El usuario abre la app → Flutter detecta Python automáticamente
   y lanza el backend
2. **Cámara**: Se abre la cámara por defecto (o la seleccionada). El backend
   procesa frames sin mostrar ventanas
3. **Video en vivo**: Flutter obtiene frames vía `/api/snapshot` y los muestra
   en tiempo real dentro de la app
4. **Detección**: InsightFace analiza cada frame (saltando N frames para
   rendimiento)
5. **Captura**: Cuando detecta un rostro con confianza ≥ 0.7 **y** respeta
   el rate limiting → captura EXACTAMENTE 1 foto
6. **Reconocimiento**: Compara el embedding facial contra los registrados en
   Supabase
7. **Registro**: Crea en la BD:
   - `access_record` (evento de detección)
   - `attendance` + `attendance_mark` (entrada o salida)
8. **Storage**: Sube la foto a Supabase Storage (visible desde el dashboard)
9. **Cooldown**: Espera 3 segundos antes de capturar a la misma persona
   nuevamente
10. **Cierre**: Al cerrar Flutter → `BackendManager.killAll()` mata el backend
    por PID file, nombre de imagen y puerto 5050

---

## 🛠️ Stack tecnológico

| Componente | Tecnología | Versión |
|-----------|-----------|---------|
| Lenguaje backend | Python | ≥ 3.8 |
| Framework web | Flask | 3.x |
| Detección facial | InsightFace (buffalo_l) | 1.x |
| Visión artificial | OpenCV | 4.x |
| Cliente HTTP | requests | 2.x |
| UI (única) | Flutter | 3.x |
| Base de datos | Supabase (PostgreSQL) | - |
| Storage | Supabase Storage | - |
| Empaquetado | PyInstaller | 6.x |

---

## 📦 Requisitos

- **Python 3.8+** en PATH (o backend empaquetado con PyInstaller)
- **Flutter 3.x** (solo para desarrollo/build)
- **Cámara USB o virtual** compatible con DShow o Media Foundation
  - DroidCam, OBS Virtual Camera, cámaras IP (via software) soportadas
- **Conexión a Internet** (para Supabase)

---

## 🚀 Instalación y ejecución

### 1. Clonar e instalar dependencias

```bash
cd reconocimiento-facial

# Dependencias Python
pip install -r backend/requirements.txt

# Dependencias Flutter
flutter pub get
```

### 2. Ejecutar en desarrollo

```bash
# Opción 1: Script todo-en-uno (recomendado)
dev.bat

# Opción 2: Manual (dos terminales)
python backend/main.py    # Terminal 1 - Backend headless (solo logs)
flutter run               # Terminal 2 - Flutter (la única UI)
```

> El backend inicia en `http://127.0.0.1:5050`
> La UI de Flutter se conecta automáticamente y muestra el video en vivo.

### 3. Detener

**Cerrar la ventana de Flutter y todo se cierra automáticamente.**
No hay que hacer nada más:
- `BackendManager.killAll()` mata el backend por PID, nombre y puerto
- `dev.bat` ejecuta triple kill de respaldo
- No quedan procesos huérfanos

---

## 📺 Video en vivo en Flutter

El backend **no abre ninguna ventana**. Todo el video se ve dentro de Flutter:

```
Flutter                    Backend Python
   │                           │
   ├── /api/snapshot (100ms) ──┤
   │   ← JPEG bytes           │
   │                           │
   ├── Image.memory() ────────┤
   │   (gaplessPlayback)      │
   │                           │
   ├── /api/status (1s) ──────┤
   │   ← JSON con estado      │
   │                           │
```

- **Frecuencia**: 200ms entre frames (~5 FPS) — balance entre fluidez y carga de CPU
- **Calidad**: JPEG calidad 85
- **Overlay**: Nombre de persona detectada + porcentaje de similitud
- **Indicador**: "EN VIVO" en la esquina superior cuando hay video

Sin ventanas OpenCV flotantes. Sin necesidad de que el usuario
interactúe con nada fuera de la app.

---

## 🎥 Selector de cámara

El selector de cámara en Flutter muestra información detallada:

- **Nombre descriptivo** (con backend y resolución)
- **Backend** (DirectShow / MediaFoundation)
- **Badge IDX** con el índice de la cámara
- **Hint** con el nombre de la cámara actual (útil si se desconectó)

### Cómo cambiar de cámara

1. Abre el dropdown en la UI de Flutter
2. Selecciona la cámara deseada de la lista
3. El backend cambia automáticamente y reinicia la captura

---

## 📷 Detección de cámaras virtuales (DroidCam, OBS)

El sistema ahora detecta cualquier dispositivo que funcione como cámara,
no solo cámaras USB físicas.

### Backends soportados

| Backend | Dispositivos | Prioridad |
|---------|-------------|-----------|
| **DirectShow (DSHOW)** | Cámaras USB físicas | Alta |
| **Media Foundation (MSMF)** | Cámaras virtuales (DroidCam, OBS, ManyCam) | Media |

### Escaneo

- Se prueban **hasta 20 índices** (antes solo 10)
- Por cada índice se prueba DSHOW primero; si falla, se prueba MSMF
- Cada cámara reporta su backend y resolución

### Si tu cámara no aparece

```bash
# Ver qué cámaras detecta el sistema
python backend/enrollment.py --list-cameras
```

Si no aparece:
1. Asegúrate de que el software de cámara virtual esté corriendo
2. Prueba con un índice más alto: `python backend/main.py` (Flutter lo maneja)
3. En la UI de Flutter, abre el selector y elige la cámara manualmente

---

## 👤 Enrollment facial

Antes de que el sistema reconozca a una persona, hay que registrar sus
embeddings faciales:

```bash
# Listar cámaras disponibles
python backend/enrollment.py --list-cameras

# Enrollment básico (elige persona del menú interactivo)
python backend/enrollment.py

# Enrollment con cámara específica y más muestras
python backend/enrollment.py --camera 1 --samples 5
```

> **Mínimo 3 muestras por persona. Recomendado: 5-10.**
> El primer embedding se marca como `is_primary=true`.
> Buena iluminación y ligeros giros de cabeza mejoran la precisión.

---

## 💀 Cierre automático de procesos

Cuando cierras la app Flutter, **TODO se cierra automáticamente**.
Sin botones, sin pasos manuales, sin procesos huérfanos.

### Triple capa de muerte (Flutter)

| Método | Qué hace |
|--------|----------|
| **1. PID file** | Lee `backend.pid` y mata por PID exacto |
| **2. Nombre** | `taskkill /F /IM python.exe` — mata TODOS los procesos Python |
| **3. Puerto 5050** | `netstat -ano` → encuentra PID en puerto 5050 → `taskkill /F /PID` |

### Triple capa de respaldo (dev.bat)

Si Flutter no alcanzó a ejecutar su cleanup (force close, crash),
`dev.bat` ejecuta el mismo triple kill después de que `flutter run` termina.

### WidgetsBindingObserver

Flutter también detecta cambios de ciclo de vida (`detached`/`inactive`)
y mata el backend inmediatamente, incluso antes de que `dispose()` se ejecute.

---

## 🔌 API HTTP (para Flutter)

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/status` | GET | Estado actual (cámara, fps, última detección) |
| `/api/cameras` | GET | Lista cámaras disponibles (con nombre y backend) |
| `/api/camera/select` | POST | Cambiar cámara activa |
| `/api/camera/test` | GET | Capturar frame de prueba y diagnosticar cámara |
| `/api/snapshot` | GET | Último frame como JPEG (para video en vivo) |
| `/api/refresh-embeddings` | POST | Recargar embeddings desde Supabase |

---

## 🛡️ Rate limiting y anti-spam

Mecanismos para evitar sobrecargar Supabase:

| Regla | Valor | Descripción |
|-------|-------|-------------|
| `MIN_CONFIDENCE_TO_CAPTURE` | 0.7 | Solo capturar si la confianza de detección es alta |
| `CAPTURE_COOLDOWN_SECONDS` | 3.0s | Esperar entre capturas de la **misma** persona |
| `FACE_LEAVE_TIMEOUT` | 5.0s | Si la persona sale del frame, resetear cache |
| `DETECTION_FRAME_SKIP` | 3 | Solo ejecutar detección cada 3 frames |

**Comportamiento:**
- 1 foto por detección perfecta → sube a Supabase
- Si la misma persona se queda quieta → NO se suben más fotos por 3s
- Si la persona sale del frame → el contador se resetea a los 5s
- Confianza baja (< 0.7) → NO se captura, se sigue detectando

---

## 🗄️ Base de datos (Supabase)

### Tablas principales

| Tabla | Propósito |
|-------|-----------|
| `companies` | Empresas registradas |
| `registered_persons` | Empleados de cada empresa |
| `face_embeddings` | Vectores faciales (512d) para reconocimiento |
| `access_records` | Cada detección facial (con foto) |
| `attendances` | Jornadas laborales (entrada/salida) |
| `attendance_marks` | Marcaciones específicas (entry/exit) |
| `company_operators` | Administradores del sistema |

### Bucket de Storage

- **Nombre:** `face-snapshots`
- **Visibilidad:** Público (para que el dashboard cargue las imágenes)
- **Contenido:** Fotos de detección + enrollment

---

## 🗺️ Roadmap SaaS

### ✅ Implementado (versión actual)

- [x] Detección facial con InsightFace
- [x] Captura de 1 foto por detección perfecta
- [x] Reconocimiento contra embeddings registrados
- [x] Rate limiting anti-spam (persona + tiempo)
- [x] Registro de access_records en Supabase
- [x] Gestión de attendances (entrada/salida)
- [x] Subida de fotos a Supabase Storage
- [x] API HTTP para integración con Flutter
- [x] UI Flutter con video en vivo (headless)
- [x] Selector de cámara con información descriptiva
- [x] Soporte para cámaras virtuales (DroidCam, OBS)
- [x] Auto-detección de Python (PATH, registro, rutas comunes)
- [x] Cierre automático de procesos (triple kill)
- [x] Empaquetado PyInstaller (backend.exe portátil)
- [x] Enrollment facial interactivo
- [x] Logs a archivo con rotación
- [x] Liberación de puerto 5050 al iniciar
- [x] Reconexión automática de cámara
- [x] Fallback a siguiente cámara disponible si una falla
- [x] Heartbeat + detección de crash del backend
- [x] WidgetsBindingObserver para cierre por ciclo de vida
- [x] Silenciado de logs HTTP werkzeug (stderr limpio)
- [x] Manejo de errores de encoding en stderr de Python
- [x] Protección anti-crash: todos los métodos de cámara con try/except
- [x] Endpoint `/api/camera/test` para diagnóstico de cámara
- [x] Escaneo de cámaras hasta índice 30 con CAP_ANY + DSHOW + MSMF
- [x] Detección de cámaras "sin señal" (no_signal) en listado

### 🔄 Pendiente para MVP

- [ ] **Reconocimiento en tiempo real sin InsightFace** — Si no hay GPU,
      el modelo buffalo_l es lento en CPU. Evaluar ONNX Runtime optimizado
      o usar un modelo más ligero (mobilefacenet).
- [ ] **Múltiples cámaras simultáneas** — Para empresas con varias entradas.
- [ ] **Cache local de embeddings** — Evitar consultar Supabase en cada
      detección. Ya se cargan al inicio, pero falta recarga periódica.
- [ ] **Health check automático con reconexión** — Si el backend se cae,
      Flutter debería reintentar automáticamente.
- [ ] **Selector de cámara con nombre real del dispositivo** — Usar
      DirectShow Properties para obtener el nombre exacto de la cámara.

### 🚀 Para SaaS completo

- [ ] **Actualización OTA** — El backend debería poder actualizarse solo
      sin intervención del usuario.
- [ ] **Configuración remota** — Umbrales, cámara por defecto, etc.,
      configurables desde el panel de administración.
- [ ] **Modo kiosko** — La app Flutter en pantalla completa, sin bordes,
      sin posibilidad de cerrar sin contraseña.
- [ ] **Registro de logs en Supabase** — Para monitoreo remoto.
- [ ] **Licenciamiento** — Validación de licencia por empresa.
- [ ] **Instalador unificado** — .exe que instala todo (backend + Flutter)
      con un solo click.
- [ ] **Soporte para cámaras IP (RTSP)** — Además de USB.

---

## 📄 Licencia

Propietaria. BioFace - Todos los derechos reservados.

---

## 👥 Equipo

Desarrollado para **Makushama IPS**.
