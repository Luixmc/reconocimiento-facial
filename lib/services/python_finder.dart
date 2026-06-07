import 'dart:io';

const _minPythonVersion = (3, 8);

/// Localiza Python en una máquina Windows con 4 estrategias de fallback.
class PythonFinder {
  static Future<String?> findPython() async {
    final bundled = _findBundledBackend();
    if (bundled != null) return bundled;

    final fromPath = await _findPythonInPath();
    if (fromPath != null) return fromPath;

    final fromRegistry = _findPythonInRegistry();
    if (fromRegistry != null) return fromRegistry;

    return _findPythonInCommonLocations();
  }

  static String? _findBundledBackend() {
    final exeDir = _getExeDir();
    final sep = Platform.pathSeparator;
    final candidates = [
      '$exeDir${sep}backend.exe',
      '$exeDir${sep}backend${sep}dist${sep}backend.exe',
    ];
    var dir = Directory(exeDir);
    for (var i = 0; i < 3; i++) {
      candidates
        ..add('${dir.path}${sep}backend.exe')
        ..add('${dir.path}${sep}backend${sep}dist${sep}backend.exe');
      dir = dir.parent;
    }
    for (final path in candidates) {
      if (File(path).existsSync()) return path;
    }
    return null;
  }

  static Future<String?> _findPythonInPath() async {
    for (final cmd in ['python', 'python3', 'py -3']) {
      try {
        final parts = cmd.split(' ');
        final result = await Process.run(
          parts.first,
          parts.length > 1 ? [...parts.skip(1), '--version'] : ['--version'],
          runInShell: true,
        );
        if (result.exitCode == 0 &&
            _isValidVersion((result.stdout as String).trim())) {
          final where = await Process.run(
            'where', [parts.first], runInShell: true);
          if (where.exitCode == 0) {
            for (final line in (where.stdout as String)
                .trim()
                .split('\n')
                .where((l) => l.trim().isNotEmpty)) {
              final path = line.trim();
              if (path.contains('WindowsApps')) continue;
              final test =
                  await Process.run(path, ['--version'], runInShell: true);
              if (test.exitCode == 0 &&
                  _isValidVersion((test.stdout as String).trim())) {
                return path;
              }
            }
          }
        }
      } catch (_) {}
    }
    return null;
  }

  static String? _findPythonInRegistry() {
    for (final hive in [
      'HKLM\\SOFTWARE\\Python\\PythonCore',
      'HKCU\\SOFTWARE\\Python\\PythonCore',
    ]) {
      try {
        final result = Process.runSync(
          'reg', ['query', hive, '/s', '/v', 'ExecutablePath', '/reg:32'],
          runInShell: true,
        );
        if (result.exitCode != 0) continue;
        for (final line in (result.stdout as String).split('\n')) {
          final match = RegExp(r'ExecutablePath\s+REG_SZ\s+(.+\.exe)')
              .firstMatch(line.trim());
          if (match == null) continue;
          final path = match.group(1)!.trim();
          if (!File(path).existsSync()) continue;
          final test =
              Process.runSync(path, ['--version'], runInShell: true);
          if (test.exitCode == 0 &&
              _isValidVersion((test.stdout as String).trim())) {
            return path;
          }
        }
      } catch (_) {}
    }
    return null;
  }

  static String? _findPythonInCommonLocations() {
    final sep = Platform.pathSeparator;
    final username = Platform.environment['USERNAME'] ?? '';
    final versions = ['314', '313', '312', '311', '310', '39', '38'];
    for (final drive in ['C:', 'D:']) {
      for (final base in [
        '$drive\\Users\\$username\\AppData\\Local\\Programs\\Python',
        '$drive\\Program Files\\Python',
        '$drive\\Python',
      ]) {
        for (final ver in versions) {
          for (final suf in ['', '-32']) {
            final path = '$base${sep}Python$ver$suf${sep}python.exe';
            if (File(path).existsSync()) return path;
          }
        }
      }
    }
    return null;
  }

  static bool _isValidVersion(String output) {
    try {
      final m = RegExp(r'Python\s+(\d+)\.(\d+)').firstMatch(output);
      if (m != null) {
        final major = int.parse(m.group(1)!);
        final minor = int.parse(m.group(2)!);
        return major > _minPythonVersion.$1 ||
            (major == _minPythonVersion.$1 && minor >= _minPythonVersion.$2);
      }
    } catch (_) {}
    return false;
  }

  static String _getExeDir() {
    try {
      return File(Platform.resolvedExecutable).parent.path;
    } catch (_) {
      return Directory.current.path;
    }
  }
}
