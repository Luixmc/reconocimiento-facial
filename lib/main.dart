import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' show pow;
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:window_manager/window_manager.dart';

import 'screens/admin_login_screen.dart';
import 'screens/admin_panel_screen.dart';
import 'screens/company_login.dart';
import 'screens/kiosk_pin_screen.dart';
import 'screens/multi_camera_screen.dart';
import 'services/auth_service.dart';
import 'services/backend_client.dart';
import 'services/backend_manager.dart';
import 'services/device_service.dart';
import 'services/source_service.dart';
import 'widgets/source_selector.dart';

const _backendHost = '127.0.0.1';
const _backendPort = 5050;

// ─── Entry point ─────────────────────────────────────────────────────────────

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Modo kiosko: pantalla completa sin bordes, no cerrable con Alt+F4
  if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
    await windowManager.ensureInitialized();
    const options = WindowOptions(
      fullScreen: false,
      titleBarStyle: TitleBarStyle.hidden,
      skipTaskbar: false,
    );
    await windowManager.waitUntilReadyToShow(options, () async {
      await windowManager.maximize();
      await windowManager.setResizable(true);
    });
  }

  runApp(const FaceRecognitionApp());
}

class FaceRecognitionApp extends StatelessWidget {
  const FaceRecognitionApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'BioFace - Reconocimiento Facial',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1A237E),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const _AppRoot(),
    );
  }
}

// ─── Raíz: decide entre login y pantalla principal ────────────────────────────

class _AppRoot extends StatefulWidget {
  const _AppRoot();

  @override
  State<_AppRoot> createState() => _AppRootState();
}

class _AppRootState extends State<_AppRoot> {
  bool _checking = true;
  bool _loggedIn  = false;
  String? _companyId;
  String? _companyName;

  @override
  void initState() {
    super.initState();
    _checkSession();
  }

  Future<void> _checkSession() async {
    final loggedIn = await AuthService.instance.loadSession();
    if (mounted) {
      setState(() {
        _checking    = false;
        _loggedIn    = loggedIn;
        _companyId   = AuthService.instance.companyId;
        _companyName = AuthService.instance.companyName;
      });
    }
  }

  void _onLoginSuccess(String companyId, String companyName) {
    setState(() {
      _loggedIn    = true;
      _companyId   = companyId;
      _companyName = companyName;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_checking) {
      return const Scaffold(
        backgroundColor: Color(0xFF0D1117),
        body: Center(
          child: CircularProgressIndicator(color: Colors.cyanAccent),
        ),
      );
    }

    if (!_loggedIn) {
      return CompanyLoginScreen(onLoginSuccess: _onLoginSuccess);
    }

    return RecognitionScreen(
      companyId:   _companyId!,
      companyName: _companyName!,
    );
  }
}

// ─── Estado ──────────────────────────────────────────────────────────────────

class _BackendState {
  String status = 'starting';
  int cameraIndex = 0;
  String cameraName = 'Cámara 0';
  String? lastPersonName;
  String? lastSnapshotUrl;
  Map<String, dynamic>? lastDetection;
  double fps = 0;
  int totalDetections = 0;
  bool supabaseOnline = false;
  int offlinePending = 0;
  String deviceUid = '';
  RecognitionLiveState recognition = const RecognitionLiveState();
}

/// Estado de reconocimiento EN VIVO reportado por el backend en cada frame
/// analizado (independiente de si la detección llegó a persistirse). Se usa
/// para mostrar mensajes/guías en tiempo real al usuario frente a la cámara.
class RecognitionLiveState {
  final bool hasFace;
  final String phase; // idle | detecting | recognized | low_confidence | unknown
  final String? personName;
  final double similarity;
  final double confidence;
  final String? quality; // alta | media | baja
  final double secondsElapsed;
  final double maxWaitSeconds;

  const RecognitionLiveState({
    this.hasFace = false,
    this.phase = 'idle',
    this.personName,
    this.similarity = 0,
    this.confidence = 0,
    this.quality,
    this.secondsElapsed = 0,
    this.maxWaitSeconds = 7,
  });

  factory RecognitionLiveState.fromJson(Map<String, dynamic>? json) {
    if (json == null) return const RecognitionLiveState();
    return RecognitionLiveState(
      hasFace: json['has_face'] as bool? ?? false,
      phase: json['phase'] as String? ?? 'idle',
      personName: json['person_name'] as String?,
      similarity: (json['similarity'] as num?)?.toDouble() ?? 0,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0,
      quality: json['quality'] as String?,
      secondsElapsed: (json['seconds_elapsed'] as num?)?.toDouble() ?? 0,
      maxWaitSeconds: (json['max_wait_seconds'] as num?)?.toDouble() ?? 7,
    );
  }
}

// ─── Pantalla principal ───────────────────────────────────────────────────────

class RecognitionScreen extends StatefulWidget {
  final String companyId;
  final String companyName;

  const RecognitionScreen({
    super.key,
    required this.companyId,
    required this.companyName,
  });

  @override
  State<RecognitionScreen> createState() => _RecognitionScreenState();
}

class _RecognitionScreenState extends State<RecognitionScreen>
    with WidgetsBindingObserver, SingleTickerProviderStateMixin {
  final _backendManager = BackendManager();
  final _backendState = _BackendState();
  final _sourceService = SourceService();
  final _sourceController = SourceSelectorController();

  Timer? _pollTimer;
  Timer? _frameTimer;

  bool _backendReady = false;
  bool _startingUp = true;
  String? _errorMessage;
  Uint8List? _currentFrame;
  int _heartbeatFails = 0;
  static const int _maxHeartbeatFails = 3;

  // Auto-restart con backoff exponencial
  int _autoRestartAttempts = 0;
  static const int _maxAutoRestartAttempts = 3;
  Timer? _autoRestartTimer;

  // Kiosk: triple tap en el título para abrir PIN
  int _titleTapCount = 0;
  Timer? _tapResetTimer;

  // Pulso de "brillo" para que las guías de reconocimiento llamen más la
  // atención (reconocido / no registrado / confirmando) sin saturar la UI.
  late final AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1100),
    )..repeat(reverse: true);
    _startBackend();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    _autoRestartTimer?.cancel();
    _tapResetTimer?.cancel();
    _pulseController.dispose();
    BackendManager.killAll();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.detached) {
      _pollTimer?.cancel();
      _frameTimer?.cancel();
      BackendManager.killAll();
    }
  }

  Future<void> _logout() async {
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    await BackendManager.gracefulShutdownAsync(pid: _backendManager.pid);
    await AuthService.instance.logout();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(
        builder: (_) => CompanyLoginScreen(
          onLoginSuccess: (id, name) {
            Navigator.of(context).pushAndRemoveUntil(
              MaterialPageRoute(
                builder: (_) => RecognitionScreen(companyId: id, companyName: name),
              ),
              (_) => false,
            );
          },
        ),
      ),
      (_) => false,
    );
  }

  /// Pide credenciales de operador admin/superadmin y, si son válidas,
  /// abre el panel de administración (registro y enrolamiento de personas).
  void _openAdminPanel() {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => AdminLoginScreen(
        companyId: widget.companyId,
        onLoginSuccess: (session) {
          Navigator.of(context).pushReplacement(MaterialPageRoute(
            builder: (_) => AdminPanelScreen(
              companyName: widget.companyName,
              operatorSession: session,
            ),
          ));
        },
      ),
    ));
  }

  Future<void> _startBackend() async {
    final error = await _backendManager.start(companyId: widget.companyId);
    if (error != null) {
      setState(() {
        _errorMessage = error;
        _startingUp = false;
      });
      return;
    }
    await _waitForBackend();
  }

  Future<void> _waitForBackend({int maxRetries = 30}) async {
    for (var i = 0; i < maxRetries; i++) {
      if (_backendManager.didCrash) {
        setState(() {
          _errorMessage = 'El backend se cerró inesperadamente.';
          _startingUp = false;
        });
        return;
      }
      try {
        final response = await http
            .get(Uri.parse('http://$_backendHost:$_backendPort/api/health'))
            .timeout(const Duration(seconds: 2));
        if (response.statusCode == 200) {
          // Cargar token antes de arrancar polling
          final dir = _backendManager.backendDir;
          if (dir != null) {
            await BackendClient.instance
                .loadToken(dir, instanceId: _backendManager.instanceId);
          }
          setState(() {
            _backendReady = true;
            _startingUp = false;
          });
          _sourceController.refresh();
          _startPolling();
          return;
        }
      } catch (_) {}
      await Future.delayed(const Duration(milliseconds: 500));
    }
    setState(() {
      _errorMessage =
          'El backend no respondió después de ${maxRetries ~/ 2} segundos.\n'
          'Verifica que no haya otro proceso usando el puerto $_backendPort.';
      _startingUp = false;
    });
  }

  void _startPolling() {
    _autoRestartAttempts = 0; // reset backoff al conectar exitosamente
    _pollTimer?.cancel();
    // Polling más frecuente para que la guía de reconocimiento (countdown,
    // calidad, fase) se sienta en vivo en vez de a saltos de 1s.
    _pollTimer = Timer.periodic(
        const Duration(milliseconds: 400), (_) => _fetchStatus());
    _fetchStatus();
    _startFrameStream();
  }

  void _startFrameStream() {
    _frameTimer?.cancel();
    // Cámara más fluida: priorizamos suavidad sobre uso de recursos.
    _frameTimer = Timer.periodic(
        const Duration(milliseconds: 80), (_) => _fetchFrame());
    _fetchFrame();
  }

  Future<void> _fetchFrame() async {
    try {
      // /api/snapshot es público — no requiere token
      final response = await http
          .get(Uri.parse('http://$_backendHost:$_backendPort/api/snapshot'))
          .timeout(const Duration(seconds: 1));
      if (response.statusCode == 200 && response.bodyBytes.isNotEmpty) {
        if (!mounted) return;
        setState(() => _currentFrame = response.bodyBytes);
      }
    } catch (_) {}
  }

  Future<void> _fetchStatus() async {
    try {
      final response = await BackendClient.instance
          .get('/api/status')
          .timeout(const Duration(seconds: 3));
      if (response.statusCode == 200) {
        _heartbeatFails = 0;
        final data = json.decode(response.body) as Map<String, dynamic>;
        setState(() {
          _backendState.status = data['status'] as String? ?? 'unknown';
          _backendState.cameraName =
              data['camera_name'] as String? ?? 'Cámara';
          _backendState.lastPersonName =
              data['last_person_name'] as String?;
          _backendState.lastSnapshotUrl =
              data['last_snapshot_url'] as String?;
          _backendState.fps = (data['fps'] as num?)?.toDouble() ?? 0;
          _backendState.totalDetections =
              data['total_detections'] as int? ?? 0;
          _backendState.lastDetection =
              data['last_detection'] as Map<String, dynamic>?;
          _backendState.supabaseOnline =
              data['supabase_online'] as bool? ?? false;
          _backendState.offlinePending =
              data['offline_pending'] as int? ?? 0;
          _backendState.deviceUid =
              data['device_uid'] as String? ?? '';
          _backendState.recognition = RecognitionLiveState.fromJson(
              data['recognition'] as Map<String, dynamic>?);
        });
      }
    } catch (_) {
      _heartbeatFails++;
      if (_heartbeatFails >= _maxHeartbeatFails && _backendManager.didCrash) {
        _scheduleAutoRestart();
      }
    }
  }

  Future<void> _restartBackend() async {
    _autoRestartTimer?.cancel();
    setState(() {
      _errorMessage = null;
      _startingUp = true;
      _backendReady = false;
      _heartbeatFails = 0;
      _currentFrame = null;
    });
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    await BackendManager.gracefulShutdownAsync(pid: _backendManager.pid);
    await _startBackend();
  }

  void _scheduleAutoRestart() {
    if (_autoRestartAttempts >= _maxAutoRestartAttempts) {
      setState(() {
        _errorMessage =
            'El backend falló $_maxAutoRestartAttempts veces consecutivas.\n'
            'Revisa la cámara o reinicia manualmente.';
        _backendReady = false;
        _currentFrame = null;
      });
      return;
    }
    _autoRestartAttempts++;
    final delaySec = pow(2, _autoRestartAttempts).toInt(); // 2s, 4s, 8s
    debugPrint(
        '[auto-restart] Intento $_autoRestartAttempts en ${delaySec}s...');
    setState(() {
      _errorMessage =
          'Backend caído. Reiniciando automáticamente (intento $_autoRestartAttempts/$_maxAutoRestartAttempts)...';
      _backendReady = false;
      _currentFrame = null;
    });
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    _autoRestartTimer = Timer(Duration(seconds: delaySec), _restartBackend);
  }

  void _onSourceChanged(SourceInfo source) {
    setState(() {
      _backendState.cameraIndex = source.index;
      _backendState.cameraName = source.name;
    });
  }

  // ─── Exportar SQLite offline ──────────────────────────────────────────────

  Future<void> _exportOfflineDb() async {
    try {
      final response = await BackendClient.instance
          .get('/api/export-offline-db')
          .timeout(const Duration(seconds: 10));
      if (response.statusCode == 404) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('No hay registros offline pendientes')),
        );
        return;
      }
      if (response.statusCode != 200) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error al exportar: ${response.statusCode}')),
        );
        return;
      }
      // Guardar en carpeta Documents del usuario
      final docsDir = Platform.environment['USERPROFILE'] ?? '.';
      final now = DateTime.now();
      final ts = '${now.year}${now.month.toString().padLeft(2, '0')}${now.day.toString().padLeft(2, '0')}';
      final dest = '$docsDir\\Documents\\offline_records_$ts.db';
      await File(dest).writeAsBytes(response.bodyBytes);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Base offline exportada a: $dest')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Error exportando: $e')),
      );
    }
  }

  // ─── Limpiar caché ────────────────────────────────────────────────────────

  Future<void> _clearCache() async {
    try {
      final response = await BackendClient.instance
          .post('/api/clear-cache')
          .timeout(const Duration(seconds: 10));
      if (response.statusCode == 200) {
        final data = json.decode(response.body) as Map<String, dynamic>;
        final mb = (data['freed_mb'] as num?)?.toStringAsFixed(2) ?? '0.00';
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Caché limpiada — $mb MB liberados')),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Error al limpiar caché: $e')),
      );
    }
  }

  // ─── Vaciar caché de rostros (BD interna de embeddings) ────────────────────

  Future<void> _clearFaceCache() async {
    // Confirmar: esta acción recarga los rostros desde Supabase
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E2E),
        title: const Text('Vaciar caché de rostros',
            style: TextStyle(color: Colors.white)),
        content: const Text(
          'Se borrará la caché interna de rostros y se recargarán desde '
          'Supabase. Útil tras registrar o actualizar personas.',
          style: TextStyle(color: Colors.white70),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar',
                style: TextStyle(color: Colors.white54)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Vaciar y recargar',
                style: TextStyle(color: Colors.cyanAccent)),
          ),
        ],
      ),
    );
    if (confirm != true) return;

    try {
      final response = await BackendClient.instance
          .post('/api/clear-face-cache')
          .timeout(const Duration(seconds: 20));
      if (!mounted) return;
      if (response.statusCode == 200) {
        final data = json.decode(response.body) as Map<String, dynamic>;
        final loaded = data['embeddings_loaded'] as int? ?? 0;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(
              'Caché de rostros vaciada — $loaded rostros recargados desde Supabase')),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error al vaciar caché de rostros: ${response.statusCode}')),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Error al vaciar caché de rostros: $e')),
      );
    }
  }

  // ─── Kiosk exit ───────────────────────────────────────────────────────────

  Future<void> _handleKioskExit() async {
    final ok = await KioskPinDialog.show(context);
    if (ok && mounted) {
      await windowManager.setFullScreen(false);
      await windowManager.setClosable(true);
      await windowManager.restore();
    }
  }

  // ─── Connection badge ─────────────────────────────────────────────────────

  Widget _buildConnectionBadge() {
    final online = _backendState.supabaseOnline;
    final pending = _backendState.offlinePending;
    return Tooltip(
      message: online
          ? 'Supabase conectado'
          : 'Sin conexión a Supabase${pending > 0 ? " — $pending registros pendientes" : ""}',
      child: Container(
        margin: const EdgeInsets.only(right: 4),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: (online ? Colors.greenAccent : Colors.redAccent)
              .withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: (online ? Colors.greenAccent : Colors.redAccent)
                .withValues(alpha: 0.5),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(
                color: online ? Colors.greenAccent : Colors.redAccent,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 5),
            Text(
              online ? 'ON' : 'OFF',
              style: TextStyle(
                color: online ? Colors.greenAccent : Colors.redAccent,
                fontSize: 11,
                fontWeight: FontWeight.bold,
              ),
            ),
            if (!online && pending > 0) ...[
              const SizedBox(width: 4),
              Text(
                '($pending)',
                style: const TextStyle(color: Colors.orangeAccent, fontSize: 10),
              ),
            ],
          ],
        ),
      ),
    );
  }

  // ─── Build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: GestureDetector(
          // Triple tap en el título → pide PIN para salir del kiosko
          onTap: () {
            _tapResetTimer?.cancel();
            _titleTapCount++;
            if (_titleTapCount >= 3) {
              _titleTapCount = 0;
              _handleKioskExit();
            } else {
              _tapResetTimer = Timer(const Duration(seconds: 2), () {
                _titleTapCount = 0;
              });
            }
          },
          child: const Text(
            'BioFace',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18),
          ),
        ),
        centerTitle: false,
        backgroundColor: const Color(0xFF1A237E),
        elevation: 0,
        automaticallyImplyLeading: false,
        actions: [
          // Indicador conexión Supabase
          if (_backendReady) _buildConnectionBadge(),
          IconButton(
            icon: const Icon(Icons.grid_view_rounded, size: 22),
            tooltip: 'Modo multi-cámara',
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => MultiCameraSetupScreen(
                    companyId: widget.companyId,
                    companyName: widget.companyName,
                  ),
                ),
              );
            },
          ),
          IconButton(
            icon: const Icon(Icons.admin_panel_settings_outlined, size: 22),
            tooltip: 'Panel de administración (registrar / enrolar personas)',
            onPressed: _openAdminPanel,
          ),
          // Menú de mantenimiento
          if (_backendReady)
            PopupMenuButton<String>(
              icon: const Icon(Icons.more_vert, size: 22),
              tooltip: 'Mantenimiento',
              onSelected: (value) {
                if (value == 'export_db') _exportOfflineDb();
                if (value == 'clear_cache') _clearCache();
                if (value == 'clear_faces') _clearFaceCache();
              },
              itemBuilder: (_) => const [
                PopupMenuItem(
                  value: 'export_db',
                  child: Row(children: [
                    Icon(Icons.download_outlined, size: 18),
                    SizedBox(width: 10),
                    Text('Exportar BD offline'),
                  ]),
                ),
                PopupMenuItem(
                  value: 'clear_cache',
                  child: Row(children: [
                    Icon(Icons.cleaning_services_outlined, size: 18),
                    SizedBox(width: 10),
                    Text('Limpiar caché'),
                  ]),
                ),
                PopupMenuItem(
                  value: 'clear_faces',
                  child: Row(children: [
                    Icon(Icons.face_retouching_off_outlined, size: 18),
                    SizedBox(width: 10),
                    Text('Vaciar caché de rostros'),
                  ]),
                ),
              ],
            ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_startingUp) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            CircularProgressIndicator(color: Colors.cyanAccent),
            SizedBox(height: 20),
            Text(
              'Iniciando sistema de reconocimiento facial...',
              style: TextStyle(color: Colors.white70, fontSize: 16),
            ),
            SizedBox(height: 8),
            Text(
              'Detectando Python e iniciando backend...',
              style: TextStyle(color: Colors.white38, fontSize: 13),
            ),
          ],
        ),
      );
    }

    if (_errorMessage != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, color: Colors.red, size: 64),
              const SizedBox(height: 16),
              Text(
                _errorMessage!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70, fontSize: 15),
              ),
              const SizedBox(height: 24),
              ElevatedButton.icon(
                onPressed: _restartBackend,
                icon: const Icon(Icons.refresh),
                label: const Text('Reintentar'),
              ),
            ],
          ),
        ),
      );
    }

    if (!_backendReady) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            CircularProgressIndicator(color: Colors.cyanAccent),
            SizedBox(height: 20),
            Text(
              'Conectando con el backend...',
              style: TextStyle(color: Colors.white70, fontSize: 16),
            ),
          ],
        ),
      );
    }

    return _buildMainUI();
  }

  Widget _buildMainUI() {
    final isLive = _backendState.status == 'running';
    final indicatorColor =
        isLive ? Colors.greenAccent : Colors.orangeAccent;
    final indicatorText = isLive ? 'EN VIVO' : 'PAUSADO';

    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          color: const Color(0xFF0D1117),
          child: Row(
            children: [
              Container(
                width: 10, height: 10,
                decoration: BoxDecoration(
                  color: indicatorColor,
                  shape: BoxShape.circle,
                  boxShadow: [
                    BoxShadow(
                      color: indicatorColor.withValues(alpha: 0.6),
                      blurRadius: 8,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Text(indicatorText,
                  style: TextStyle(
                      color: indicatorColor,
                      fontWeight: FontWeight.bold,
                      fontSize: 12)),
              const Spacer(),
              Text('${_backendState.fps.toStringAsFixed(0)} FPS',
                  style: const TextStyle(color: Colors.white60, fontSize: 12)),
              const SizedBox(width: 16),
              Text('Detectados: ${_backendState.totalDetections}',
                  style: const TextStyle(color: Colors.white60, fontSize: 12)),
              const SizedBox(width: 12),
              Tooltip(
                message: 'Empresa: ${widget.companyName}\nCerrar sesión',
                child: InkWell(
                  onTap: _logout,
                  borderRadius: BorderRadius.circular(20),
                  child: const Padding(
                    padding: EdgeInsets.all(4),
                    child: Icon(Icons.logout, size: 18, color: Colors.white38),
                  ),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                SourceSelector(
                  sourceService: _sourceService,
                  controller: _sourceController,
                  onSourceChanged: _onSourceChanged,
                ),
                const SizedBox(height: 16),
                Expanded(child: _buildPreviewArea()),
                const SizedBox(height: 16),
                _buildDetectionCard(),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildPreviewArea() {
    final hasFrame = _currentFrame != null && _currentFrame!.isNotEmpty;
    final hasOverlay = _backendState.lastPersonName != null;

    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF0D1117),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white12),
      ),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (hasFrame)
            Image.memory(
              _currentFrame!,
              fit: BoxFit.contain,
              gaplessPlayback: true,
              errorBuilder: (ctx, err, st) => _buildPlaceholder(),
            )
          else
            _buildPlaceholder(),
          if (hasOverlay)
            Positioned(
              bottom: 0, left: 0, right: 0,
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      Colors.transparent,
                      Colors.black.withValues(alpha: 0.85),
                    ],
                  ),
                ),
                child: Row(children: [
                  const Icon(Icons.person,
                      color: Colors.cyanAccent, size: 20),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _backendState.lastPersonName!,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  if (_backendState.lastDetection != null)
                    Text(
                      '${((_backendState.lastDetection!['similarity'] as num? ?? 0) * 100).toStringAsFixed(0)}%',
                      style: const TextStyle(
                        color: Colors.greenAccent,
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                ]),
              ),
            ),
          if (hasFrame)
            Positioned(
              top: 8, left: 8,
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: Colors.black54,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 6, height: 6,
                      decoration: const BoxDecoration(
                        color: Colors.greenAccent,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 4),
                    const Text('EN VIVO',
                        style: TextStyle(
                          color: Colors.greenAccent,
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                        )),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildPlaceholder() {
    final isLive = _backendState.status == 'running';
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(
          isLive ? Icons.videocam : Icons.videocam_off,
          color: Colors.white24,
          size: 48,
        ),
        const SizedBox(height: 8),
        Text(
          isLive ? 'Conectando cámaras...' : 'Cámara no disponible',
          style: const TextStyle(color: Colors.white24, fontSize: 14),
        ),
      ],
    );
  }

  /// Tarjeta-guía con el estado de reconocimiento EN VIVO (no depende de que
  /// la detección se haya persistido — se actualiza en cada frame analizado).
  Widget _buildDetectionCard() {
    final rec = _backendState.recognition;

    String stateKey;
    Widget card;

    if (!rec.hasFace) {
      stateKey = 'no_face';
      card = _buildGuideCard(
        icon: Icons.center_focus_weak,
        iconColor: Colors.white38,
        color: Colors.white38,
        title: 'Colóquese frente a la cámara',
        subtitle: 'El reconocimiento empieza apenas se detecte un rostro',
      );
    } else {
      switch (rec.phase) {
        case 'recognized':
          stateKey = 'recognized';
          card = _buildGuideCard(
            icon: Icons.check_circle,
            iconColor: Colors.greenAccent,
            color: Colors.greenAccent,
            title: '¡${rec.personName ?? "Persona"} reconocido/a! ✓',
            subtitle:
                'ACCESO AUTORIZADO · Calidad ${_qualityLabel(rec.quality)} '
                '(${(rec.similarity * 100).toStringAsFixed(0)}% de similitud)',
            pulse: true,
            big: true,
          );
          break;

        case 'low_confidence':
          stateKey = 'low_confidence';
          card = _buildGuideCard(
            icon: Icons.warning_amber_rounded,
            iconColor: Colors.orangeAccent,
            color: Colors.orangeAccent,
            title: '${rec.personName ?? "Posible coincidencia"} — confirmando…',
            subtitle: 'Calidad ${_qualityLabel(rec.quality)} '
                '(${(rec.similarity * 100).toStringAsFixed(0)}%). '
                '¡Acérquese y mire directo a la cámara para confirmar!',
            pulse: true,
          );
          break;

        case 'unknown':
          stateKey = 'unknown';
          card = _buildGuideCard(
            icon: Icons.person_off_outlined,
            iconColor: Colors.redAccent,
            color: Colors.redAccent,
            title: '⚠ Persona no registrada',
            subtitle: 'No se tomará el registro de entrada/salida. '
                'Si debería estar registrada, contacte al administrador para inscribirla.',
            pulse: true,
          );
          break;

        case 'detecting':
        default:
          stateKey = 'detecting';
          final maxWait = rec.maxWaitSeconds <= 0 ? 7.0 : rec.maxWaitSeconds;
          final progress = (rec.secondsElapsed / maxWait).clamp(0.0, 1.0);
          card = _buildGuideCard(
            icon: Icons.search,
            iconColor: Colors.cyanAccent,
            color: Colors.cyanAccent,
            title: 'Reconociendo…',
            subtitle: 'Mire directo a la cámara y manténgase quieto '
                '(${rec.secondsElapsed.toStringAsFixed(0)}s / ${maxWait.toStringAsFixed(0)}s)',
            progress: progress,
          );
      }
    }

    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 300),
      switchInCurve: Curves.easeOutCubic,
      switchOutCurve: Curves.easeInCubic,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.97, end: 1.0).animate(animation),
          child: child,
        ),
      ),
      child: KeyedSubtree(key: ValueKey(stateKey), child: card),
    );
  }

  String _qualityLabel(String? quality) {
    switch (quality) {
      case 'alta':
        return 'buena';
      case 'media':
        return 'regular — acérquese más';
      case 'baja':
        return 'baja — mejore la iluminación';
      default:
        return '—';
    }
  }

  /// Tarjeta de guía/notificación en pantalla.
  /// [pulse] hace que el brillo del borde y el ícono "respiren" para llamar
  /// la atención en estados que requieren acción del usuario (reconocido,
  /// confirmando, no registrado). [big] resalta aún más el texto y el ícono
  /// para el momento clave: "acceso autorizado".
  Widget _buildGuideCard({
    required IconData icon,
    required Color iconColor,
    required Color color,
    required String title,
    required String subtitle,
    double? progress,
    bool pulse = false,
    bool big = false,
  }) {
    final iconSize = big ? 40.0 : 30.0;
    final titleSize = big ? 21.0 : 17.0;
    final subtitleSize = big ? 15.0 : 13.5;

    Widget buildCard(double glow) {
      return Container(
        padding: EdgeInsets.all(big ? 20 : 16),
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [
              const Color(0xFF1E1E2E),
              Color.lerp(const Color(0xFF1E1E2E), color, 0.16 + 0.10 * glow)!,
            ],
          ),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: color.withValues(alpha: 0.35 + 0.4 * glow),
            width: 1.5,
          ),
          boxShadow: [
            BoxShadow(
              color: color.withValues(alpha: 0.15 + 0.30 * glow),
              blurRadius: 16 + 16 * glow,
              spreadRadius: big ? 1.5 : 0.5,
            ),
          ],
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              padding: EdgeInsets.all(big ? 12 : 9),
              decoration: BoxDecoration(
                color: iconColor.withValues(alpha: 0.15 + 0.10 * glow),
                borderRadius: BorderRadius.circular(10),
                boxShadow: [
                  BoxShadow(
                    color: iconColor.withValues(alpha: 0.25 + 0.35 * glow),
                    blurRadius: 14,
                  ),
                ],
              ),
              child: Icon(icon, color: iconColor, size: iconSize),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: TextStyle(
                          color: Colors.white,
                          fontSize: titleSize,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 0.2)),
                  const SizedBox(height: 5),
                  Text(subtitle,
                      style: TextStyle(
                          color: color,
                          fontSize: subtitleSize,
                          fontWeight: big ? FontWeight.w600 : FontWeight.normal,
                          height: 1.3)),
                  if (progress != null) ...[
                    const SizedBox(height: 10),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(5),
                      child: LinearProgressIndicator(
                        value: progress,
                        minHeight: 5,
                        backgroundColor: Colors.white12,
                        valueColor: AlwaysStoppedAnimation<Color>(color),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      );
    }

    if (!pulse) return buildCard(0.0);

    return AnimatedBuilder(
      animation: _pulseController,
      builder: (context, _) => buildCard(_pulseController.value),
    );
  }
}
