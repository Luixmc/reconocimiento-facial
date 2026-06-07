import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'python_finder.dart';

const _backendHost = '127.0.0.1';
const _backendPort = 5050;

/// Gestiona el ciclo de vida del proceso Python del backend.
class BackendManager {
  Process? _process;
  bool _killed = false;
  bool _crashed = false;

  /// Puerto en que corre esta instancia. Por defecto 5050.
  int port;

  /// Índice de cámara que gestiona esta instancia. Por defecto 1.
  int cameraIndex;

  /// Frames a saltar entre cada ejecución de InsightFace.
  /// Mayor valor = menos CPU. Recomendado 5-8 para setups de 10+ cámaras.
  int frameSkip;

  /// Calidad JPEG del snapshot (0-100). Menor = menos banda, más velocidad.
  int snapshotQuality;

  BackendManager({
    this.port = _backendPort,
    this.cameraIndex = 1,
    this.frameSkip = 3,
    this.snapshotQuality = 85,
  });

  bool get isRunning => _process != null;
  bool get didCrash => _crashed;
  int? get pid => _process?.pid;

  /// ID de instancia único basado en el puerto.
  String get instanceId => '$port';

  /// Directorio donde vive backend/main.py (para leer el token).
  String? get backendDir {
    final script = _findBackendScript();
    if (script == null) return null;
    return File(script).parent.path;
  }

  Future<String?> start({String? companyId}) async {
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

    try {
      final args = isBundled ? <String>[] : [scriptPath!];
      final workDir = isBundled
          ? File(pythonPath).parent.path
          : File(scriptPath!).parent.path;

      final env = <String, String>{'PYTHONUNBUFFERED': '1'};
      if (companyId != null && companyId.isNotEmpty) {
        env['COMPANY_ID'] = companyId;
      }
      env['FLASK_PORT'] = '$port';
      env['CAMERA_INDEX'] = '$cameraIndex';
      env['INSTANCE_ID'] = instanceId;
      env['FRAME_SKIP'] = '$frameSkip';
      env['SNAPSHOT_QUALITY'] = '$snapshotQuality';

      _process = await Process.start(
        pythonPath, args,
        workingDirectory: workDir,
        environment: env,
        mode: ProcessStartMode.normal,
      );

      _process!.stdout
          .transform(utf8.decoder)
          .listen((data) => debugPrint('[backend] $data'));
      _process!.stderr
          .transform(const Utf8Decoder(allowMalformed: true))
          .listen((data) => debugPrint('[backend-err] $data'));

      _process!.exitCode.then((code) {
        debugPrint('Backend terminado (código: $code)');
        if (!_killed && code != 0) {
          _crashed = true;
        }
        _process = null;
      });

      return null;
    } catch (e) {
      return 'Error al iniciar el backend: $e';
    }
  }

  Future<void> kill() async {
    if (_process == null) return;
    _killed = true;
    final pid = _process!.pid;
    await gracefulShutdownAsync(pid: pid, port: port, instanceId: instanceId);
    _process = null;
  }

  static String? _findBackendScript() {
    final sep = Platform.pathSeparator;
    final exeDir = _getExeDir();
    final cwd = Directory.current.path;
    final candidates = <String>[
      '$exeDir${sep}backend${sep}main.py',
      '$cwd${sep}backend${sep}main.py',
    ];
    var dir = Directory(exeDir);
    for (var i = 0; i < 5; i++) {
      candidates.add('${dir.path}${sep}backend${sep}main.py');
      dir = dir.parent;
    }
    dir = Directory(cwd);
    for (var i = 0; i < 3; i++) {
      candidates.add('${dir.path}${sep}backend${sep}main.py');
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

  static Future<void> gracefulShutdownAsync({
    int? pid,
    int port = _backendPort,
    String? instanceId,
    Duration gracePeriod = const Duration(seconds: 4),
  }) async {
    debugPrint('[shutdown] Iniciando graceful shutdown (port=$port)...');
    bool apiOk = false;
    try {
      final response = await http
          .post(Uri.parse('http://$_backendHost:$port/api/shutdown'))
          .timeout(const Duration(seconds: 2));
      apiOk = response.statusCode == 200;
    } catch (e) {
      debugPrint('[shutdown] API no respondió: $e');
    }

    if (!apiOk || pid == null) {
      _nuclearKill(pid);
      return;
    }

    final deadline = DateTime.now().add(gracePeriod);
    bool processDied = false;
    while (DateTime.now().isBefore(deadline)) {
      await Future.delayed(const Duration(milliseconds: 300));
      try {
        final check = await Process.run(
          'tasklist', ['/FI', 'PID eq $pid', '/NH'], runInShell: true);
        if (!(check.stdout as String).contains('$pid')) {
          processDied = true;
          break;
        }
      } catch (_) {
        processDied = true;
        break;
      }
    }

    if (!processDied) {
      debugPrint('[shutdown] Nuclear kill (no respondió en ${gracePeriod.inSeconds}s)');
      _nuclearKill(pid, instanceId: instanceId ?? '$port');
    }
  }

  static void gracefulShutdown({int? pid, int port = _backendPort, String? instanceId}) {
    () async {
      try {
        await http
            .post(Uri.parse('http://$_backendHost:$port/api/shutdown'))
            .timeout(const Duration(milliseconds: 500));
      } catch (_) {}
    }();
    sleep(const Duration(milliseconds: 600));
    _nuclearKill(pid, instanceId: instanceId ?? '$port');
  }

  static bool _cleanedUp = false;

  /// Mata TODAS las instancias del backend (usado al cerrar la app).
  /// [ports] lista de puertos a liberar; por defecto solo 5050.
  static void killAll({List<int> ports = const [_backendPort]}) {
    if (_cleanedUp) return;
    _cleanedUp = true;
    debugPrint('[killAll] Limpieza total (puertos: $ports)...');

    final base = 'backend';
    final sep = Platform.pathSeparator;

    // Matar por PID file de cada instancia
    for (final port in ports) {
      final pidFile = File('$base${sep}backend_$port.pid');
      if (pidFile.existsSync()) {
        try {
          final pid = int.parse(pidFile.readAsStringSync().trim());
          Process.runSync('taskkill', ['/F', '/T', '/PID', '$pid'],
              runInShell: true);
        } catch (_) {}
      }
    }

    for (final img in ['python.exe', 'backend.exe']) {
      try {
        Process.runSync('taskkill', ['/F', '/IM', img], runInShell: true);
      } catch (_) {}
    }

    // Liberar todos los puertos usados
    try {
      final result = Process.runSync('netstat', ['-ano'], runInShell: true);
      if (result.exitCode == 0) {
        for (final port in ports) {
          for (final line in (result.stdout as String).split('\n')) {
            if (line.contains(':$port') && line.contains('LISTENING')) {
              final parts = line.trim().split(RegExp(r'\s+'));
              if (parts.length >= 5 && parts.last != '0') {
                Process.runSync('taskkill', ['/F', '/PID', parts.last],
                    runInShell: true);
              }
            }
          }
        }
      }
    } catch (_) {}

    for (final port in ports) {
      _cleanupFiles(instanceId: '$port');
    }
  }

  static void _nuclearKill(int? pid, {String? instanceId}) {
    if (pid != null) {
      try {
        Process.runSync('taskkill', ['/F', '/T', '/PID', '$pid'],
            runInShell: true);
      } catch (_) {}
    }
    final id = instanceId ?? '$_backendPort';
    final pidFile =
        File('backend${Platform.pathSeparator}backend_$id.pid');
    if (pidFile.existsSync()) {
      try {
        final filePid = int.parse(pidFile.readAsStringSync().trim());
        if (filePid != pid) {
          Process.runSync('taskkill', ['/F', '/PID', '$filePid'],
              runInShell: true);
        }
      } catch (_) {}
    }
    for (final img in ['python.exe', 'backend.exe']) {
      try {
        Process.runSync('taskkill', ['/F', '/IM', img], runInShell: true);
      } catch (_) {}
    }
    _cleanupFiles(instanceId: id);
  }

  static void _cleanupFiles({String? instanceId}) {
    final base = 'backend';
    final sep = Platform.pathSeparator;
    final id = instanceId ?? '$_backendPort';
    for (final name in [
      'backend_$id.pid',
      'backend_$id.log',
      'backend_$id.token',
    ]) {
      try {
        final f = File('$base$sep$name');
        if (f.existsSync()) f.deleteSync();
      } catch (_) {}
    }
    for (final dir in ['__pycache__', 'build', 'dist']) {
      try {
        final d = Directory('$base$sep$dir');
        if (d.existsSync()) d.deleteSync(recursive: true);
      } catch (_) {}
    }
  }
}
