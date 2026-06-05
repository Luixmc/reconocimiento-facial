import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import 'services/source_service.dart';
import 'widgets/source_selector.dart';

// ────────────────────────────────────────────────────────────────────────────
// Configuración
// ────────────────────────────────────────────────────────────────────────────

const _backendHost = '127.0.0.1';
const _backendPort = 5050;

// Versión mínima de Python requerida
const _minPythonVersion = (3, 8);

// ────────────────────────────────────────────────────────────────────────────
// PythonFinder: detecta Python en cualquier máquina Windows
// ────────────────────────────────────────────────────────────────────────────

class PythonFinder {
  /// Busca Python siguiendo esta prioridad:
  /// 1. backend.exe empaquetado (PyInstaller)
  /// 2. PATH del sistema (`where python`)
  /// 3. Registro de Windows (HKLM + HKCU)
  /// 4. Rutas comunes de instalación
  static Future<String?> findPython() async {
    // 1. Buscar backend empaquetado
    final bundled = _findBundledBackend();
    if (bundled != null) {
      debugPrint('[PythonFinder] Usando backend empaquetado: $bundled');
      return bundled;
    }

    // 2. Buscar en PATH (prueba varios candidatos)
    final fromPath = await _findPythonInPath();
    if (fromPath != null) {
      debugPrint('[PythonFinder] Python en PATH: $fromPath');
      return fromPath;
    }

    // 3. Buscar en el Registro de Windows
    final fromRegistry = _findPythonInRegistry();
    if (fromRegistry != null) {
      debugPrint('[PythonFinder] Python en registro: $fromRegistry');
      return fromRegistry;
    }

    // 4. Buscar en rutas comunes
    final fromCommon = _findPythonInCommonLocations();
    if (fromCommon != null) {
      debugPrint('[PythonFinder] Python en ruta común: $fromCommon');
      return fromCommon;
    }

    return null;
  }

  /// Busca el backend empaquetado con PyInstaller
  static String? _findBundledBackend() {
    final exeDir = _getExeDir();
    final candidates = [
      '$exeDir${Platform.pathSeparator}backend.exe',
      '$exeDir${Platform.pathSeparator}backend'
          '${Platform.pathSeparator}dist${Platform.pathSeparator}backend.exe',
    ];

    // Buscar hacia arriba por si el exe está en subdirectorios
    var dir = Directory(exeDir);
    for (var i = 0; i < 3; i++) {
      candidates.add('${dir.path}${Platform.pathSeparator}backend.exe');
      candidates.add(
          '${dir.path}${Platform.pathSeparator}backend'
          '${Platform.pathSeparator}dist${Platform.pathSeparator}backend.exe');
      dir = dir.parent;
    }

    for (final path in candidates) {
      if (File(path).existsSync()) return path;
    }
    return null;
  }

  /// Busca python.exe en el PATH probando múltiples comandos
  static Future<String?> _findPythonInPath() async {
    final commands = ['python', 'python3', 'py -3'];

    for (final cmd in commands) {
      try {
        final result = await Process.run(
          cmd.contains(' ') ? cmd.split(' ').first : cmd,
          cmd.contains(' ') ? ['-3', '--version'] : ['--version'],
          runInShell: true,
        );
        if (result.exitCode == 0) {
          final version = (result.stdout as String).trim();
          if (_isValidVersion(version)) {
            // Obtener la ruta completa
            final whereResult = await Process.run(
              'where',
              [cmd.split(' ').first],
              runInShell: true,
            );
            if (whereResult.exitCode == 0) {
              final lines = (whereResult.stdout as String)
                  .trim()
                  .split('\n')
                  .where((l) => l.trim().isNotEmpty)
                  .toList();
              // Probar cada candidato hasta encontrar uno que funcione
              for (final line in lines) {
                final pythonPath = line.trim();
                // Ignorar el stub de Microsoft Store (WindowsApps)
                if (pythonPath.contains('WindowsApps')) continue;
                final test = await Process.run(
                  pythonPath,
                  ['--version'],
                  runInShell: true,
                );
                if (test.exitCode == 0 &&
                    _isValidVersion((test.stdout as String).trim())) {
                  return pythonPath;
                }
              }
            }
          }
        }
      } catch (_) {}
    }
    return null;
  }

  static bool _isValidVersion(String versionOutput) {
    // Parsear "Python 3.12.5" -> (3, 12)
    try {
      final match = RegExp(r'Python\s+(\d+)\.(\d+)').firstMatch(versionOutput);
      if (match != null) {
        final major = int.parse(match.group(1)!);
        final minor = int.parse(match.group(2)!);
        if (major > _minPythonVersion.$1 ||
            (major == _minPythonVersion.$1 &&
                minor >= _minPythonVersion.$2)) {
          return true;
        }
      }
    } catch (_) {}
    return false;
  }

  /// Busca Python en el Registro de Windows (HKLM + HKCU)
  static String? _findPythonInRegistry() {
    try {
      final result = Process.runSync(
        'reg',
        [
          'query',
          'HKLM\\SOFTWARE\\Python\\PythonCore',
          '/s',
          '/v',
          'ExecutablePath',
          '/reg:32',
        ],
        runInShell: true,
      );

      if (result.exitCode == 0) {
        final output = (result.stdout as String);
        final lines = output.split('\n');
        for (final line in lines) {
          final match = RegExp(r'ExecutablePath\s+REG_SZ\s+(.+\.exe)')
              .firstMatch(line.trim());
          if (match != null) {
            final path = match.group(1)!.trim();
            if (File(path).existsSync()) {
              // Verificar versión
              final test = Process.runSync(path, ['--version'],
                  runInShell: true);
              if (test.exitCode == 0 &&
                  _isValidVersion((test.stdout as String).trim())) {
                return path;
              }
            }
          }
        }
      }
    } catch (_) {}

    // Intentar también en HKCU (usuario actual)
    try {
      final result = Process.runSync(
        'reg',
        [
          'query',
          'HKCU\\SOFTWARE\\Python\\PythonCore',
          '/s',
          '/v',
          'ExecutablePath',
          '/reg:32',
        ],
        runInShell: true,
      );

      if (result.exitCode == 0) {
        final output = (result.stdout as String);
        final lines = output.split('\n');
        for (final line in lines) {
          final match = RegExp(r'ExecutablePath\s+REG_SZ\s+(.+\.exe)')
              .firstMatch(line.trim());
          if (match != null) {
            final path = match.group(1)!.trim();
            if (File(path).existsSync()) {
              final test = Process.runSync(path, ['--version'],
                  runInShell: true);
              if (test.exitCode == 0 &&
                  _isValidVersion((test.stdout as String).trim())) {
                return path;
              }
            }
          }
        }
      }
    } catch (_) {}

    return null;
  }

  /// Busca Python en ubicaciones de instalación comunes
  static String? _findPythonInCommonLocations() {
    final drives = ['C:', 'D:'];
    final versions = [
      '314', '313', '312', '311', '310',
      '39', '38', '37',
    ];
    final basePaths = [
      r'\Users\%s\AppData\Local\Programs\Python',
      r'\Program Files\Python',
      r'\Python',
    ];

    try {
      final username = Platform.environment['USERNAME'] ?? '';
      for (final drive in drives) {
        for (final base in basePaths) {
          final basePath = base.contains('%s')
              ? '$drive$base'.replaceAll('%s', username)
              : '$drive$base';
          for (final ver in versions) {
            for (final suffix in ['', '-32']) {
              final pythonPath =
                  '$basePath\\Python$ver$suffix\\python.exe';
              if (File(pythonPath).existsSync()) return pythonPath;
            }
          }
        }
      }
    } catch (_) {}
    return null;
  }

  static String _getExeDir() {
    try {
      return File(Platform.resolvedExecutable).parent.path;
    } catch (_) {
      return Directory.current.path;
    }
  }
}

// ────────────────────────────────────────────────────────────────────────────
// BackendManager: gestiona el ciclo de vida del proceso Python
// ────────────────────────────────────────────────────────────────────────────

class BackendManager {
  Process? _process;
  bool _killed = false;
  bool _crashed = false;

  bool get isRunning => _process != null;
  bool get didCrash => _crashed;
  bool get isKilled => _killed;

  /// Inicia el backend. Retorna null si ok, o un mensaje de error.
  Future<String?> start() async {
    if (_process != null) return null;
    _killed = false;
    _crashed = false;

    final pythonPath = await PythonFinder.findPython();
    if (pythonPath == null) {
      return 'Python no encontrado.\n\n'
          'Instala Python 3.8+ desde python.org\n'
          'marcando "Add Python to PATH" durante la instalación.\n\n'
          'O ejecuta: python backend\\build_backend.py\n'
          'para generar un backend.exe portátil.';
    }

    final isBundled = pythonPath.endsWith('backend.exe');
    final scriptPath = _findBackendScript();
    if (!isBundled && scriptPath == null) {
      return 'No se encontró backend/main.py.\n'
          'Asegúrate de que la carpeta backend/ esté presente.';
    }

    debugPrint('[Backend] Usando: $pythonPath');
    if (!isBundled) debugPrint('[Backend] Script: $scriptPath');

    try {
      final args = isBundled ? <String>[] : [scriptPath!];
      final workDir = isBundled
          ? File(pythonPath).parent.path
          : File(scriptPath!).parent.path;

      _process = await Process.start(
        pythonPath,
        args,
        workingDirectory: workDir,
        environment: {'PYTHONUNBUFFERED': '1'},
        mode: ProcessStartMode.normal,
      );

      _process!.stdout
          .transform(utf8.decoder)
          .listen((data) => debugPrint('[backend] $data'));
      // stderr puede contener binary data de librerias nativas
      // (OpenCV, InsightFace, etc.). Usar allowMalformed evita que el
      // decoder lance excepciones y silencia el stream, permitiendo ver
      // el traceback completo incluso si hay bytes corruptos.
      _process!.stderr
          .transform(const Utf8Decoder(allowMalformed: true))
          .listen((data) => debugPrint('[backend-err] $data'));

      // Watchdog: si el proceso termina inesperadamente, marcarlo
      _process!.exitCode.then((code) {
        debugPrint('Backend terminado (código: $code)');
        if (!_killed && code != 0) {
          _crashed = true;
          _process = null;
        } else if (_killed) {
          _process = null;
        }
      });

      return null;
    } catch (e) {
      return 'Error al iniciar el backend: $e';
    }
  }

  String? _findBackendScript() {
    final candidates = <String>[];
    final exeDir = _getExeDir();
    final cwd = Directory.current.path;

    candidates.add(
        '$exeDir${Platform.pathSeparator}backend${Platform.pathSeparator}main.py');
    candidates.add(
        '$cwd${Platform.pathSeparator}backend${Platform.pathSeparator}main.py');

    var dir = Directory(exeDir);
    for (var i = 0; i < 5; i++) {
      candidates.add(
          '${dir.path}${Platform.pathSeparator}backend'
          '${Platform.pathSeparator}main.py');
      dir = dir.parent;
    }
    dir = Directory(cwd);
    for (var i = 0; i < 3; i++) {
      candidates.add(
          '${dir.path}${Platform.pathSeparator}backend'
          '${Platform.pathSeparator}main.py');
      dir = dir.parent;
    }

    for (final path in candidates) {
      if (File(path).existsSync()) return path;
    }
    return null;
  }

  static String _getExeDir() {
    try {
      return File(Platform.resolvedExecutable).parent.path;
    } catch (_) {
      return Directory.current.path;
    }
  }

  /// Mata el backend de forma controlada llamando primero la API.
  Future<void> kill() async {
    if (_process == null) return;
    _killed = true;
    final pid = _process!.pid;
    await gracefulShutdownAsync(pid: pid);
    _process = null;
  }

  /// Nuclear kill: método privado que mata procesos y limpia archivos.
  static void _nuclearKill(int? pid) {
    // 1. Por PID directo (más quirúrgico)
    if (pid != null) {
      try {
        debugPrint('[shutdown] Taskkill por PID $pid...');
        Process.runSync('taskkill', ['/F', '/T', '/PID', '$pid'],
            runInShell: true);
      } catch (_) {}
    }

    // 2. Por PID file
    final pidFile = File('backend${Platform.pathSeparator}backend.pid');
    if (pidFile.existsSync()) {
      try {
        final filePid = int.parse(pidFile.readAsStringSync().trim());
        if (filePid != pid) {
          debugPrint('[shutdown] Taskkill por PID file: $filePid...');
          Process.runSync('taskkill', ['/F', '/PID', '$filePid'],
              runInShell: true);
        }
      } catch (_) {}
    }

    // 3. Por nombre de imagen (python.exe + backend.exe)
    for (final img in ['python.exe', 'backend.exe']) {
      try {
        debugPrint('[shutdown] Taskkill por imagen: $img...');
        Process.runSync('taskkill', ['/F', '/IM', img], runInShell: true);
      } catch (_) {}
    }

    // 4. Por puerto 5050 (último recurso)
    try {
      final result = Process.runSync('netstat', ['-ano'], runInShell: true);
      if (result.exitCode == 0) {
        for (final line in (result.stdout as String).split('\n')) {
          if (line.contains(':5050') && line.contains('LISTENING')) {
            final parts = line.trim().split(RegExp(r'\s+'));
            if (parts.length >= 5) {
              final portPid = parts.last;
              if (portPid != '0' && portPid != '$pid') {
                debugPrint('[shutdown] Taskkill por puerto 5050 - PID: $portPid');
                Process.runSync('taskkill', ['/F', '/PID', portPid],
                    runInShell: true);
              }
            }
          }
        }
      }
    } catch (_) {}

    // 5. Limpiar archivos residuales
    _cleanupFiles();

    debugPrint('[shutdown] Nuclear kill completado');
  }

  /// Limpia todos los archivos residuales del backend.
  static void _cleanupFiles() {
    final baseDir = 'backend';
    final sep = Platform.pathSeparator;

    // PID file
    final pidFile = File('$baseDir${sep}backend.pid');
    try {
      if (pidFile.existsSync()) {
        pidFile.deleteSync();
        debugPrint('[cleanup] PID file eliminado');
      }
    } catch (_) {}

    // Log file
    final logFile = File('$baseDir${sep}backend.log');
    try {
      if (logFile.existsSync()) {
        logFile.deleteSync();
        debugPrint('[cleanup] Log file eliminado');
      }
    } catch (_) {}

    // __pycache__ directorios
    try {
      if (Directory('$baseDir${sep}__pycache__').existsSync()) {
        _deleteDirectoryRecursive(Directory('$baseDir${sep}__pycache__'));
        debugPrint('[cleanup] __pycache__ eliminado');
      }
    } catch (_) {}

    // build/ y dist/ generados por PyInstaller
    for (final dir in ['build', 'dist']) {
      try {
        final dirPath = Directory('$baseDir${sep}$dir');
        if (dirPath.existsSync()) {
          _deleteDirectoryRecursive(dirPath);
          debugPrint('[cleanup] $dir/ eliminado');
        }
      } catch (_) {}
    }

    // Archivos .spec de PyInstaller
    try {
      final specFile = File('$baseDir${sep}backend.spec');
      if (specFile.existsSync()) {
        specFile.deleteSync();
        debugPrint('[cleanup] backend.spec eliminado');
      }
    } catch (_) {}

    debugPrint('[cleanup] Archivos residuales eliminados');
  }

  /// Elimina un directorio recursivamente (equivalente a rm -rf).
  /// Usa deleteSync(recursive: true) de Dart que es más simple y rápido.
  static void _deleteDirectoryRecursive(Directory dir) {
    if (!dir.existsSync()) return;
    try {
      dir.deleteSync(recursive: true);
    } catch (_) {}
  }

  /// Cierre limpio con await. Úsalo en _restartBackend() y similares.
  /// 1. Llama /api/shutdown → Python corre su finally (libera cámara, borra PID file)
  /// 2. Espera hasta [gracePeriod] a que el proceso muera solo
  /// 3. Si no murió, nuclear kill como fallback
  static Future<void> gracefulShutdownAsync({
    int? pid,
    Duration gracePeriod = const Duration(seconds: 4),
  }) async {
    debugPrint('[shutdown] Iniciando graceful shutdown...');

    // Paso 1: Pedir al backend que se apague limpiamente
    bool apiOk = false;
    try {
      final response = await http
          .post(
            Uri.parse('http://$_backendHost:$_backendPort/api/shutdown'),
          )
          .timeout(const Duration(seconds: 2));
      apiOk = response.statusCode == 200;
      debugPrint('[shutdown] API /api/shutdown respondió: ${response.statusCode}');
    } catch (e) {
      // El backend ya puede estar muerto — no es error fatal
      debugPrint('[shutdown] API no respondió (backend ya cerrado?): $e');
    }

    if (!apiOk || pid == null) {
      // Si la API falló o no tenemos PID, nuclear directo
      _nuclearKill(pid);
      return;
    }

    // Paso 2: Esperar a que el proceso muera solo (Python corre su finally)
    final deadline = DateTime.now().add(gracePeriod);
    bool processDied = false;

    while (DateTime.now().isBefore(deadline)) {
      await Future.delayed(const Duration(milliseconds: 300));
      // Verificar si el proceso ya no existe
      try {
        final check = await Process.run(
          'tasklist',
          ['/FI', 'PID eq $pid', '/NH'],
          runInShell: true,
        );
        final output = check.stdout as String;
        if (!output.contains('$pid')) {
          processDied = true;
          debugPrint('[shutdown] Proceso $pid terminó limpiamente');
          break;
        }
      } catch (_) {
        processDied = true; // Si tasklist falla, asumimos que murió
        break;
      }
    }

    // Paso 3: Fallback nuclear solo si el proceso sobrevivió la espera
    if (!processDied) {
      debugPrint('[shutdown] Proceso $pid no respondió en ${gracePeriod.inSeconds}s — nuclear kill');
      _nuclearKill(pid);
    }
  }

  /// Versión síncrona para usar en dispose() donde no puedes hacer await.
  /// Llama la API en fire-and-forget y luego nuclear kill con delay mínimo.
  /// No es perfecta pero es mejor que matar directo sin avisar al backend.
  static void gracefulShutdown({int? pid}) {
    debugPrint('[shutdown] Graceful shutdown (sync)...');

    // Fire-and-forget correcto en Dart: unawaited con try/catch interno.
    () async {
      try {
        await http
            .post(Uri.parse('http://$_backendHost:$_backendPort/api/shutdown'))
            .timeout(const Duration(milliseconds: 500));
      } catch (_) {}
    }();

    sleep(const Duration(milliseconds: 600));
    _nuclearKill(pid);
  }

  /// Guard para evitar ejecutar killAll() múltiples veces.
  static bool _cleanedUp = false;

  /// Mata TODO: todos los procesos, libera puerto 5050, elimina archivos.
  /// No depende de _process — usa PID file, nombre de imagen, puerto, y más.
  /// Es SINCRONO para usarse en dispose(). Solo se ejecuta una vez.
  static void killAll() {
    if (_cleanedUp) {
      debugPrint('[killAll] Saltando — ya se ejecutó la limpieza en detached');
      return;
    }
    _cleanedUp = true;

    debugPrint('[killAll] === INICIANDO LIMPIEZA TOTAL ===');

    // 1. Matar por PID file (backend.pid)
    final pidFile = File('backend${Platform.pathSeparator}backend.pid');
    if (pidFile.existsSync()) {
      try {
        final pid = int.parse(pidFile.readAsStringSync().trim());
        debugPrint('[killAll] Matando por PID file: $pid');
        Process.runSync('taskkill', ['/F', '/T', '/PID', '$pid'],
            runInShell: true);
      } catch (_) {}
    }

    // 2. Matar por nombre de imagen (python.exe + backend.exe)
    for (final img in ['python.exe', 'backend.exe']) {
      try {
        debugPrint('[killAll] Matando por imagen: $img...');
        Process.runSync('taskkill', ['/F', '/IM', img], runInShell: true);
      } catch (_) {}
    }

    // 3. Matar por puerto 5050 (netstat + taskkill)
    try {
      final result = Process.runSync('netstat', ['-ano'], runInShell: true);
      if (result.exitCode == 0) {
        final lines = (result.stdout as String).split('\n');
        for (final line in lines) {
          if (line.contains(':5050') && line.contains('LISTENING')) {
            final parts = line.trim().split(RegExp(r'\s+'));
            if (parts.length >= 5) {
              final pid = parts.last;
              if (pid != '0') {
                debugPrint('[killAll] Matando por puerto 5050 - PID: $pid');
                Process.runSync(
                    'taskkill', ['/F', '/PID', pid], runInShell: true);
              }
            }
          }
        }
      }
    } catch (_) {}

    // 4. Limpiar archivos residuales
    _cleanupFiles();

    debugPrint('[killAll] === LIMPIEZA TOTAL COMPLETADA ===');
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Estado
// ────────────────────────────────────────────────────────────────────────────

class _BackendState {
  String status = 'starting';
  int cameraIndex = 0;
  String cameraName = 'Cámara 0';
  String? lastPersonName;
  String? lastSnapshotUrl;
  Map<String, dynamic>? lastDetection;
  double fps = 0;
  int totalDetections = 0;
}

// ────────────────────────────────────────────────────────────────────────────
// App
// ────────────────────────────────────────────────────────────────────────────

void main() {
  WidgetsFlutterBinding.ensureInitialized();
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
      home: const RecognitionScreen(),
    );
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Pantalla principal
// ────────────────────────────────────────────────────────────────────────────

class RecognitionScreen extends StatefulWidget {
  const RecognitionScreen({super.key});

  @override
  State<RecognitionScreen> createState() => _RecognitionScreenState();
}

class _RecognitionScreenState extends State<RecognitionScreen>
    with WidgetsBindingObserver {
  final _backendManager = BackendManager();
  final _backendState = _BackendState();
  final _sourceService = SourceService(host: _backendHost, port: _backendPort);
  Timer? _pollTimer;
  Timer? _frameTimer;
  final _sourceController = SourceSelectorController();
  bool _backendReady = false;
  bool _startingUp = true;
  String? _errorMessage;
  Uint8List? _currentFrame;

  // Heartbeat: contador de fallos consecutivos
  int _heartbeatFails = 0;
  static const int _maxHeartbeatFails = 3;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _startBackend();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    // Cierre TOTAL: mata procesos, libera puerto 5050, elimina archivos
    BackendManager.killAll();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // En Windows, 'inactive' se emite durante el arranque y al perder foco.
    // Solo hacer limpieza total en 'detached' (cierre real de la app).
    if (state == AppLifecycleState.detached) {
      debugPrint('[lifecycle] detached — iniciando limpieza total');
      _pollTimer?.cancel();
      _frameTimer?.cancel();
      BackendManager.killAll();
    }
  }

  Future<void> _startBackend() async {
    final error = await _backendManager.start();
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
      // Si el proceso ya se cayó mientras esperábamos
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
          debugPrint('Backend listo!');
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
    _pollTimer?.cancel();
    _pollTimer =
        Timer.periodic(const Duration(seconds: 1), (_) => _fetchStatus());
    _fetchStatus();
    _startFrameStream();
  }

  void _startFrameStream() {
    _frameTimer?.cancel();
    _frameTimer = Timer.periodic(
        const Duration(milliseconds: 200), (_) => _fetchFrame());
    _fetchFrame();
  }

  Future<void> _fetchFrame() async {
    try {
      final response = await http
          .get(
            Uri.parse('http://$_backendHost:$_backendPort/api/snapshot'),
          )
          .timeout(const Duration(seconds: 1));
      if (response.statusCode == 200 && response.bodyBytes.isNotEmpty) {
        if (!mounted) return;
        setState(() {
          _currentFrame = response.bodyBytes;
        });
      }
    } catch (_) {
      // Error fetching frame — continuar con el último frame
    }
  }

  Future<void> _fetchStatus() async {
    try {
      final response = await http
          .get(Uri.parse('http://$_backendHost:$_backendPort/api/status'))
          .timeout(const Duration(seconds: 3));
      if (response.statusCode == 200) {
        _heartbeatFails = 0; // Resetear contador
        final data = json.decode(response.body) as Map<String, dynamic>;
        setState(() {
          _backendState.status = data['status'] as String? ?? 'unknown';
          _backendState.cameraName = data['camera_name'] as String? ?? 'Cámara';
          _backendState.lastPersonName = data['last_person_name'] as String?;
          _backendState.lastSnapshotUrl = data['last_snapshot_url'] as String?;
          _backendState.fps = (data['fps'] as num?)?.toDouble() ?? 0;
          _backendState.totalDetections = data['total_detections'] as int? ?? 0;
          _backendState.lastDetection =
              data['last_detection'] as Map<String, dynamic>?;
        });
      }
    } catch (_) {
      // Heartbeat: contar fallos consecutivos
      _heartbeatFails++;
      if (_heartbeatFails >= _maxHeartbeatFails) {
        // El backend dejó de responder
        if (_backendManager.didCrash) {
          setState(() {
            _errorMessage = 'El backend se cerró inesperadamente.\n'
                'Puedes reintentar para reiniciarlo.';
            _backendReady = false;
            _currentFrame = null;
          });
          _pollTimer?.cancel();
          _frameTimer?.cancel();
        }
      }
    }
  }

  Future<void> _restartBackend() async {
    setState(() {
      _errorMessage = null;
      _startingUp = true;
      _backendReady = false;
      _heartbeatFails = 0;
      _currentFrame = null;
    });
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    await BackendManager.gracefulShutdownAsync(pid: _backendManager._process?.pid);
    await _startBackend();
  }

  void _onSourceChanged(SourceInfo source) {
    setState(() {
      _backendState.cameraIndex = source.index;
      _backendState.cameraName = source.name;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: const Text(
          'BioFace - Reconocimiento Facial',
          style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18),
        ),
        centerTitle: true,
        backgroundColor: const Color(0xFF1A237E),
        elevation: 0,
        automaticallyImplyLeading: false,
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
    final indicatorColor = isLive ? Colors.greenAccent : Colors.orangeAccent;
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
            ],
          ),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                _buildCameraSelector(),
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

  Widget _buildCameraSelector() {
    return SourceSelector(
      sourceService: _sourceService,
      controller: _sourceController,
      onSourceChanged: _onSourceChanged,
    );
  }

  Widget _buildPreviewArea() {
    final bool hasFrame = _currentFrame != null && _currentFrame!.isNotEmpty;
    final bool hasOverlay = _backendState.lastPersonName != null;

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
          // Video en vivo desde /api/snapshot (headless backend)
          if (hasFrame)
            Image.memory(
              _currentFrame!,
              fit: BoxFit.contain,
              gaplessPlayback: true,
              errorBuilder: (context, error, stackTrace) => _buildPlaceholder(),
            )
          else
            _buildPlaceholder(),

          // Overlay inferior con nombre de la persona detectada
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
                  const Icon(Icons.person, color: Colors.cyanAccent, size: 20),
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

          // Indicador de "EN VIVO" en la esquina superior
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
                      width: 6,
                      height: 6,
                      decoration: const BoxDecoration(
                        color: Colors.greenAccent,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 4),
                    const Text(
                      'EN VIVO',
                      style: TextStyle(
                        color: Colors.greenAccent,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildPlaceholder() {
    final bool isLive = _backendState.status == 'running';
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

  Widget _buildDetectionCard() {
    final detection = _backendState.lastDetection;
    if (detection == null) {
      return Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF1E1E2E),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.white10),
        ),
        child: const Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.info_outline, color: Colors.white24, size: 18),
            SizedBox(width: 8),
            Text('Colóquese frente a la cámara para iniciar',
                style: TextStyle(color: Colors.white38, fontSize: 14)),
          ],
        ),
      );
    }
    final personName = detection['full_name'] as String? ?? 'Desconocido';
    final result = detection['result'] as String? ?? 'unknown';
    final similarity = (detection['similarity'] as num?) ?? 0;
    final isAuthorized = result == 'authorized';
    final resultColor = isAuthorized ? Colors.greenAccent : Colors.orangeAccent;
    final resultIcon = isAuthorized ? Icons.check_circle : Icons.help;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E2E),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: isAuthorized
              ? Colors.greenAccent.withValues(alpha: 0.3)
              : Colors.orangeAccent.withValues(alpha: 0.3),
        ),
      ),
      child: Row(children: [
        Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            color: resultColor.withValues(alpha: 0.15),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(resultIcon, color: resultColor, size: 28),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(personName,
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              Text(
                'Similitud: ${(similarity * 100).toStringAsFixed(1)}% · '
                '${isAuthorized ? "Autorizado" : "No registrado"}',
                style: TextStyle(color: resultColor, fontSize: 13),
              ),
            ],
          ),
        ),
        if (_backendState.status == 'running')
          Container(
            width: 8, height: 8,
            decoration: BoxDecoration(
              color: Colors.greenAccent,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: Colors.greenAccent.withValues(alpha: 0.5),
                  blurRadius: 4,
                ),
              ],
            ),
          ),
      ]),
    );
  }
}
