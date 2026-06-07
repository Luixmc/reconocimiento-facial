# BioFace - Sistema de Reconocimiento Facial

> **Módulo de captura facial para empleados** — parte del ecosistema BioFace.
>
> Este componente corre directamente en los puestos de entrada de los empleados.
> Login por ID de empresa (UUID). Detecta el rostro, identifica a la persona,
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
- [Login de empresa](#login-de-empresa)
- [Seguridad y multi-tenant](#seguridad-y-multi-tenant)
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
| **Login por empresa** | La app valida el ID de empresa contra Supabase antes de arrancar |
| **Multi-tenant** | COMPANY_ID se inyecta al backend como env var en tiempo de ejecución |

---

## 🔄 Flujo de operación

1. **Login**: El usuario abre la app → pantalla de login → ingresa el UUID de
   empresa → Flutter valida contra Supabase (empresa + licencia activa) → sesión
   guardada en SharedPreferences
2. **Inicio**: Backend arranca con `COMPANY_ID` inyectado como variable de entorno
3. **Cámara**: Se abre la cámara por defecto (o la seleccionada). El backend
   procesa frames sin mostrar ventanas
4. **Video en vivo**: Flutter obtiene frames vía `/api/snapshot` y los muestra
   en tiempo real dentro de la app
5. **Detección**: InsightFace analiza cada frame (saltando N frames para
   rendimiento)
6. **Captura**: Cuando detecta un rostro con confianza ≥ 0.7 **y** respeta
   el rate limiting → captura EXACTAMENTE 1 foto
7. **Reconocimiento**: Compara el embedding facial contra los registrados en
   Supabase
8. **Registro**: Crea en la BD:
   - `access_record` (evento de detección)
   - `attendance` + `attendance_mark` (entrada o salida)
9. **Storage**: Sube la foto a Supabase Storage (visible desde el dashboard)
10. **Cooldown**: Espera 3 segundos antes de capturar a la misma persona
    nuevamente
11. **Cierre**: Al cerrar Flutter → `BackendManager.killAll()` mata el backend
    por PID file, nombre de imagen y puerto 5050

---

## 🛠️ Stack tecnológico

| Componente | Tecnología | Versión |
|-----------|-----------|---------|
| Lenguaje backend | Python | ≥ 3.8 |
| Framework web | Flask | 3.x |
| Detección facial | InsightFace (buffalo_s) | 1.x |
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

Todos los endpoints excepto `/api/health`, `/api/snapshot` y `/api/shutdown`
requieren cabecera `X-Backend-Token` con el token generado al arrancar el backend.

| Endpoint | Método | Auth | Descripción |
|----------|--------|------|-------------|
| `/api/health` | GET | ✗ | Health check |
| `/api/snapshot` | GET | ✗ | Último frame como JPEG (video en vivo) |
| `/api/status` | GET | ✓ | Estado actual (cámara, fps, última detección) |
| `/api/cameras` | GET | ✓ | Lista cámaras disponibles (con nombre y backend) |
| `/api/camera/select` | POST | ✓ | Cambiar cámara activa |
| `/api/camera/test` | GET | ✓ | Capturar frame de prueba y diagnosticar cámara |
| `/api/source/status` | GET | ✓ | Estado de la fuente de video |
| `/api/source/list` | GET | ✓ | Lista todas las fuentes disponibles |
| `/api/source/select` | POST | ✓ | Seleccionar fuente (USB, DroidCam, RTSP) |
| `/api/source/scan-droidcam` | POST | ✓ | Escanear red local buscando DroidCam |
| `/api/refresh-embeddings` | POST | ✓ | Recargar embeddings desde Supabase |
| `/api/persons` | GET | ✓ | Listar personas disponibles para enrollment |
| `/api/enroll` | POST | ✓ | Enrollment facial remoto (multipart: person_id + image) |
| `/api/shutdown` | POST | ✗ | Apagar backend limpiamente |

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
| `access_records` | Cada detección facial (resultado + similitud) |
| `attendances` | Jornadas laborales (entrada/salida) |
| `attendance_marks` | Marcaciones específicas (entry/exit) |
| `attendance_rules` | Reglas horarias por empresa |
| `person_schedules` | Horario asignado por persona |
| `company_operators` | Administradores del sistema |
| `areas` | Áreas/departamentos de la empresa |
| `devices` | Dispositivos de captura registrados |
| `system_settings` | Configuración por empresa |

### Tablas de infraestructura SaaS (nuevas)

| Tabla | Propósito |
|-------|-----------|
| `backend_logs` | Logs remotos del backend (WARNING+), con isolación por empresa |
| `licenses` | Licencias activas por empresa; validadas al arrancar |
| `app_versions` | Versiones publicadas para OTA; comparación semver automática |

### Bucket de Storage

- **Nombre:** `face-snapshots`
- **Visibilidad:** Público (para que el dashboard cargue las imágenes)
- **Contenido:** Fotos de detección + enrollment

### Configuración de entorno

El backend usa `.env` (nunca commitear):

```env
SUPABASE_URL=https://tu-proyecto.supabase.co
SUPABASE_KEY=tu_service_role_key
COMPANY_ID=uuid-de-tu-empresa
```

Copiar `backend/.env.example` como punto de partida.

---

## 🔐 Login de empresa

Al abrir la app, se muestra una pantalla de login antes de arrancar el backend.

### Flujo de autenticación

```
App abre
   │
   ├── SharedPreferences → ¿hay sesión guardada?
   │       │
   │      SÍ ──────────────────────────────────────────┐
   │       │                                            │
   │       NO                                           ▼
   │       │                               RecognitionScreen
   │       ▼                               (backend arranca con
   │  CompanyLoginScreen                    COMPANY_ID correcto)
   │  (campo UUID de empresa)
   │       │
   │       ▼
   │  validate_company_login() ──► Supabase RPC (anon key)
   │       │                       · companies: ¿existe?
   │       │                       · licenses:  ¿activa? ¿no expirada?
   │       │
   │      ERROR ──► mensaje en pantalla
   │       │
   │      OK  ──► guardar en SharedPreferences ──► RecognitionScreen
```

### Seguridad del login

- El ID de empresa es un UUID v4 — 2¹²² combinaciones posibles, no adivinable
- La validación usa la función `validate_company_login()` con SECURITY DEFINER:
  el anon key no puede leer las tablas directamente; solo obtiene el resultado
  de la función (nombre de empresa + estado de licencia)
- La sesión se persiste localmente; el backend siempre recibe el `COMPANY_ID`
  como variable de entorno en cada arranque, nunca hardcodeado
- Logout: apaga el backend limpiamente y borra la sesión de disco

### Archivos involucrados

| Archivo | Rol |
|---------|-----|
| `lib/services/auth_service.dart` | Lógica de validación RPC + persistencia SharedPreferences |
| `lib/screens/company_login.dart` | Pantalla de login (UUID input + paste + error UI) |
| `lib/services/backend_manager.dart` | `start({companyId})` → pasa `COMPANY_ID` como env var |
| `lib/main.dart` | `_AppRoot` → decide entre login y pantalla principal al arrancar |

---

## 🏢 Seguridad y multi-tenant

### Row Level Security (RLS)

Todas las tablas tienen RLS activado con políticas por rol:

| Rol JWT | Acceso |
|---------|--------|
| `root` | Todas las empresas (superadmin del SaaS) |
| `admin` | Solo su empresa (lectura + escritura) |
| `viewer` | Solo su empresa (solo lectura) |
| `anon` | Solo via función `validate_company_login()` |

El rol se lee del JWT via `jwt_role()` → `user_metadata.role` o `app_metadata.role`.
La empresa se obtiene via `get_my_company_id()` → `profiles.company_id` donde `id = auth.uid()`.

### Multi-tenant por empresa

- El backend Python usa `service_role` (bypassa RLS) con filtros explícitos `WHERE company_id = COMPANY_ID`
- El `COMPANY_ID` se inyecta como env var al proceso Python desde Flutter al momento de login
- La función `set_company_context(uuid)` está disponible vía RPC para triggers y funciones que necesiten el contexto de empresa en operaciones batch
- El panel admin y dashboard usan JWT con `company_id` en `profiles` → RLS se aplica automáticamente

### Tablas cubiertas por RLS

Todas las 20 tablas del schema tienen RLS activado y políticas definidas:
`companies`, `company_operators`, `registered_persons`, `face_embeddings`,
`face_embedding_candidates`, `biometric_test_records`, `attendance_rules`,
`attendances`, `access_records`, `attendance_marks`, `attendance_corrections`,
`profiles`, `devices`, `areas`, `system_settings`, `person_schedules`,
`person_day_conventions`, `backend_logs`, `licenses`, `app_versions`.

---

## 🗺️ Roadmap SaaS

### ✅ Implementado (versión actual)

**Core**
- [x] Detección facial con InsightFace buffalo_s (CPU + CUDA fallback)
- [x] Captura de 1 foto por detección perfecta
- [x] Reconocimiento por similitud coseno vectorizado (numpy matrix multiply — O(1) vs O(N) loop)
- [x] Rate limiting anti-spam (persona + tiempo)
- [x] Registro de access_records en Supabase
- [x] Gestión de attendances (entrada/salida)
- [x] Subida de fotos a Supabase Storage

**Rendimiento y multi-cámara**
- [x] Cache de embeddings en pickle (offline survival — Supabase no requerida al arrancar)
- [x] JPEG encode una sola vez en hilo lector; `/api/snapshot` lee bytes ya codificados
- [x] Frame skip configurable por cámara (no ejecutar InsightFace en cada frame)
- [x] Matriz de embeddings pre-normalizada para dot product directo
- [x] Hasta 4 cámaras simultáneas (`MultiBackendManager`) con startup en lotes paralelos
- [x] Prioridad de cámara por zona hospitalaria (Alta/Normal/Baja — distinto FPS y calidad JPEG)
- [x] Auto-restart con backoff exponencial (2s/4s/8s) si el backend de una cámara crashea
- [x] Grid responsivo en UI multi-cámara: 1 col / 2 col / agrupación por zona
- [x] Modo foco (tap para expandir una cámara individual)
- [x] Preset hospitalario: 4 zonas preconfigadas (Urgencias, UCI, Recepción, Consultas)

**Arquitectura**
- [x] Backend dividido en módulos: `api_routes`, `attendance_service`, `enroll_service`, `remote_logger`, `startup_checks`, `device_manager`, `offline_queue`
- [x] Flutter dividido en módulos: `BackendClient`, `BackendManager`, `MultiBackendManager`, `PythonFinder`, `SourceService`, `DeviceService`
- [x] Inyección de dependencias via dict en `register_routes(app, deps)` — sin globals
- [x] Credenciales en `.env` (eliminadas del código fuente); falla explícita si faltan
- [x] `COMPANY_ID` en `.env` — preparado para multi-tenant

**Seguridad**
- [x] Token de sesión `secrets.token_hex(32)` generado en cada arranque
- [x] Middleware `X-Backend-Token` en todos los endpoints protegidos
- [x] Endpoints públicos explícitos: `/api/health`, `/api/snapshot`, `/api/shutdown`
- [x] `.env` y `backend.token` en `.gitignore`

**UI y cámara**
- [x] UI Flutter con video en vivo headless (sin ventanas OpenCV)
- [x] Selector de cámara con información descriptiva
- [x] Soporte para cámaras virtuales (DroidCam, OBS)
- [x] Escaneo de cámaras hasta índice 30 con CAP_ANY + DSHOW + MSMF
- [x] Fuentes de video unificadas: USB, DroidCam, RTSP (vía `SourceManager`)
- [x] Endpoint `/api/camera/scan-droidcam` para descubrir DroidCam en la red
- [x] Modo kiosko: pantalla completa, titlebar oculto, triple-tap → PIN para salir
- [x] Indicador de conexión ON/OFF (verde/rojo) con contador de registros offline pendientes
- [x] Menú de mantenimiento: exportar BD offline y limpiar caché

**Ciclo de vida**
- [x] Auto-detección de Python (PATH, registro Windows, rutas comunes)
- [x] Cierre automático triple kill (PID file + nombre proceso + puerto)
- [x] Liberación de puerto 5050 al iniciar si está ocupado
- [x] Reconexión automática de cámara + fallback a siguiente disponible
- [x] Heartbeat + detección de crash del backend en Flutter
- [x] WidgetsBindingObserver para cierre por ciclo de vida
- [x] Limpieza periódica de caché cada 6 horas (`__pycache__`, logs rotados, .tmp)

**Enrollment**
- [x] Enrollment facial interactivo (`enrollment.py`)
- [x] Enrollment remoto via API HTTP (`POST /api/enroll` multipart) — desde Flutter
- [x] `EnrollService` reutiliza modelo InsightFace ya cargado (sin doble carga)
- [x] Refresh automático de embeddings tras enrollment exitoso

**SaaS — Identidad y dispositivos**
- [x] Login de empresa por **ID de acceso** formato `XXXX-XXXX-XXXX` (no UUID adivinable)
- [x] `company_access_id` generado desde panel admin con botón, copiable al clipboard
- [x] Licencias por empresa: activar/desactivar + fecha de expiración desde panel admin
- [x] `device_uid` permanente por PC (`BF-XXXXXXXXXXXXXXXX`) generado en primer login
- [x] Registro automático del dispositivo en Supabase (`devices`) al arrancar
- [x] Heartbeat del dispositivo a Supabase cada 60s (`heartbeat_device` RPC)
- [x] Eliminación de `device_uid` desde panel admin (para tests/reset)
- [x] Roles de cámara por índice: `entrada` / `salida` / `ambas` configurables desde panel admin y dashboard

**SaaS — Sincronización offline**
- [x] Cola offline SQLite (`backend/offline_records.db`) — registros que no pudieron subir
- [x] Sync automático cada 10 minutos si hay internet disponible
- [x] Sync inmediato al detectar reconexión a Supabase
- [x] Export de la BD offline con botón en la app (descarga a `Documents/`)
- [x] Fallback a cola offline si Supabase no responde durante detección

**SaaS — Configuración remota**
- [x] `system_settings` en Supabase: parámetros key-value por empresa
- [x] `get_device_config` RPC: el backend descarga `system_settings` + `camera_configs` en cada heartbeat
- [x] `mark_stale_devices_offline` RPC: marca offline dispositivos sin heartbeat en N segundos

**Infraestructura SaaS**
- [x] Logging remoto a Supabase (`SupabaseLogHandler` — queue + daemon thread, WARNING+)
- [x] Validación de licencia al arrancar (`startup_checks.py`, fail-open)
- [x] Check OTA con comparación semver — notifica si hay versión más nueva
- [x] Tablas `backend_logs`, `licenses`, `app_versions` en Supabase
- [x] Enums corregidos: `access_result` + `not_found`; `attendance_status` + `closed`

**Multi-tenant y seguridad**
- [x] Sesión persistida en SharedPreferences; logout limpia sesión y apaga backend
- [x] `COMPANY_ID` inyectado como env var al proceso Python en cada arranque
- [x] RLS completo en las 20 tablas — políticas por rol `root`/`admin`/`viewer`/`anon`
- [x] Políticas de escritura para `admin` en todas las tablas de negocio
- [x] Función `validate_company_login(access_id)` accesible por anon key (SECURITY DEFINER)
- [x] Función `upsert_device` + `heartbeat_device` + `get_device_config` como RPCs SECURITY DEFINER
- [x] Endpoints admin: `/api/admin/access-records`, `/api/admin/attendances`, `/api/admin/logs`

**Panel admin (`panel-admin-reconocimiento-facial`)**
- [x] Tabla de empresas con columnas: ID de acceso, Licencia, Creada, Acciones
- [x] Botón "Generar ID" por empresa → diálogo con ID generado + copiar al clipboard
- [x] Gestión de licencias: activar/desactivar + selector de fecha de expiración
- [x] Dispositivos: chips de detecciones/día, versión app, UID copiable, botón reset UID
- [x] Config de roles de cámara por dispositivo: SegmentedButton Entrada/Salida/Ambas × 4 cámaras

**Dashboard (`dashboard-reconocimiento-facial`)**
- [x] Página "Dispositivos" con tarjetas: estado online/offline, versión, detecciones, UID
- [x] Config de roles de cámara directamente desde el dashboard del operador
- [x] Diálogo de roles: SegmentedButton Entrada/Salida/Ambas por cámara, guarda en Supabase

### 🚀 Para SaaS vendible — qué falta

#### Bloqueantes críticos (sin esto no se puede vender)

- [ ] **Instalador .exe unificado** — El cliente no puede instalar Python, Flutter y dependencias
      manualmente. Necesita un setup.exe que instale todo silenciosamente.
- [ ] **Descarga + instalación OTA automática** — La notificación ya existe (`startup_checks.py`),
      pero falta que el backend descargue el nuevo `.exe` y lo instale sin que el cliente
      toque nada.
- [ ] **Onboarding de empresa en el panel admin** — Flujo para que un nuevo cliente cree su
      empresa, reciba su ID de acceso, registre a sus empleados y llegue a la primera
      detección exitosa. Sin este flujo guiado el producto es invendible.

#### Necesario para retención y confianza

- [ ] **Alertas por Supabase Realtime o email** — Notificar al administrador si el dispositivo
      lleva más de X minutos sin detectar nadie (puede indicar que la cámara falló o que
      el backend está caído).
- [ ] **Nombre real de dispositivo de cámara** — Via DirectShow Properties en vez de índice
      numérico; mejora la UX al configurar roles de cámara.

#### Importante para escalar

- [ ] **Soporte RTSP/cámaras IP nativas con UI** — Muchas empresas ya tienen cámaras IP
      instaladas. La infraestructura de `SourceManager` ya lo soporta, falta UI de
      configuración desde la app.
- [ ] **Firma y verificación del instalador** — Para distribución segura; sin firma digital
      Windows Defender bloquea el `.exe`.
- [ ] **Dashboard: métricas por dispositivo** — Gráficas de detecciones por hora/día por
      puesto (hoy el dashboard muestra asistencias globales, no por dispositivo).

---

## 📄 Licencia

Propietaria. BioFace - Todos los derechos reservados.

---

## 👥 Equipo

Desarrollado para **Makushama IPS**.
