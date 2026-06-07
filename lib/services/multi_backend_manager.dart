import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'backend_client.dart';
import 'backend_manager.dart';

// ─── Constantes ──────────────────────────────────────────────────────────────

const int kBasePort = 5050;
const int kMaxCameras = 4;
const int _startBatchSize = 2; // lotes más pequeños = arranque más estable
const String _prefsKey = 'multi_camera_config_v2';

// Zonas hospitalarias predefinidas
const List<String> kHospitalZones = [
  'Recepción / Admisión',
  'Urgencias',
  'UCI / Cuidados Intensivos',
  'Quirófano',
  'Hospitalización',
  'Consultas Externas',
  'Laboratorio',
  'Radiología / Rayos X',
  'Farmacia',
  'Maternidad',
  'Emergencias Pediátricas',
  'Morgue / Patología',
  'Cafetería / Comedor',
  'Estacionamiento',
  'Otra',
];

// ─── Prioridad de cámara ─────────────────────────────────────────────────────

enum CameraPriority {
  /// Alta: área crítica (UCI, Urgencias, Quirófano).
  /// Frame rate máximo incluso cuando no está en foco.
  high,

  /// Normal: resto de áreas clínicas.
  normal,

  /// Baja: áreas no clínicas (cafetería, estacionamiento).
  /// Frame rate reducido para ahorrar CPU.
  low,
}

extension CameraPriorityExt on CameraPriority {
  String get label {
    switch (this) {
      case CameraPriority.high:
        return 'Alta (área crítica)';
      case CameraPriority.normal:
        return 'Normal';
      case CameraPriority.low:
        return 'Baja';
    }
  }

  /// Intervalo de refresco de frame en ms según prioridad y si está enfocada.
  int frameIntervalMs({bool focused = false}) {
    if (focused) return 200;
    switch (this) {
      case CameraPriority.high:
        return 333; // ~3 FPS aunque no esté enfocada
      case CameraPriority.normal:
        return 500; // ~2 FPS
      case CameraPriority.low:
        return 1000; // ~1 FPS
    }
  }

  /// Frame skip recomendado según prioridad (más alto = menos CPU).
  int get recommendedFrameSkip {
    switch (this) {
      case CameraPriority.high:
        return 3;
      case CameraPriority.normal:
        return 5;
      case CameraPriority.low:
        return 8;
    }
  }

  /// Calidad JPEG recomendada.
  int get recommendedJpegQuality {
    switch (this) {
      case CameraPriority.high:
        return 85;
      case CameraPriority.normal:
        return 75;
      case CameraPriority.low:
        return 65;
    }
  }

  String toJson() => name;
  static CameraPriority fromJson(String s) =>
      CameraPriority.values.firstWhere((e) => e.name == s,
          orElse: () => CameraPriority.normal);
}

// ─── Configuración de un slot de cámara ──────────────────────────────────────

class CameraSlotConfig {
  final int port;
  final int cameraIndex;
  final String label;
  final String zone;
  final CameraPriority priority;
  final int frameSkip;
  final int snapshotQuality;

  const CameraSlotConfig({
    required this.port,
    required this.cameraIndex,
    this.label = '',
    this.zone = '',
    this.priority = CameraPriority.normal,
    this.frameSkip = 5,
    this.snapshotQuality = 75,
  });

  CameraSlotConfig copyWith({
    int? port,
    int? cameraIndex,
    String? label,
    String? zone,
    CameraPriority? priority,
    int? frameSkip,
    int? snapshotQuality,
  }) =>
      CameraSlotConfig(
        port: port ?? this.port,
        cameraIndex: cameraIndex ?? this.cameraIndex,
        label: label ?? this.label,
        zone: zone ?? this.zone,
        priority: priority ?? this.priority,
        frameSkip: frameSkip ?? this.frameSkip,
        snapshotQuality: snapshotQuality ?? this.snapshotQuality,
      );

  Map<String, dynamic> toJson() => {
        'port': port,
        'cameraIndex': cameraIndex,
        'label': label,
        'zone': zone,
        'priority': priority.toJson(),
        'frameSkip': frameSkip,
        'snapshotQuality': snapshotQuality,
      };

  factory CameraSlotConfig.fromJson(Map<String, dynamic> j) => CameraSlotConfig(
        port: j['port'] as int,
        cameraIndex: j['cameraIndex'] as int,
        label: j['label'] as String? ?? '',
        zone: j['zone'] as String? ?? '',
        priority: CameraPriorityExt.fromJson(j['priority'] as String? ?? 'normal'),
        frameSkip: j['frameSkip'] as int? ?? 5,
        snapshotQuality: j['snapshotQuality'] as int? ?? 75,
      );

  /// Genera configuración óptima automáticamente según zona.
  static CameraSlotConfig autoForZone({
    required int port,
    required int cameraIndex,
    required String zone,
    String label = '',
  }) {
    CameraPriority priority;
    if (_criticalZones.any((z) => zone.contains(z))) {
      priority = CameraPriority.high;
    } else if (_lowPriorityZones.any((z) => zone.contains(z))) {
      priority = CameraPriority.low;
    } else {
      priority = CameraPriority.normal;
    }
    return CameraSlotConfig(
      port: port,
      cameraIndex: cameraIndex,
      label: label.isEmpty ? zone : label,
      zone: zone,
      priority: priority,
      frameSkip: priority.recommendedFrameSkip,
      snapshotQuality: priority.recommendedJpegQuality,
    );
  }

  static const _criticalZones = ['UCI', 'Urgencias', 'Quirófano', 'Emergencias', 'Cuidados'];
  static const _lowPriorityZones = ['Cafetería', 'Estacionamiento', 'Comedor'];
}

// ─── Progreso de inicio ──────────────────────────────────────────────────────

class StartupProgress {
  final int total;
  final int launched;
  final int ready;
  final int failed;
  final String currentAction;

  const StartupProgress({
    this.total = 0,
    this.launched = 0,
    this.ready = 0,
    this.failed = 0,
    this.currentAction = '',
  });

  double get fraction => total == 0 ? 0 : (ready + failed) / total;
  bool get isDone => total > 0 && (ready + failed) >= total;
}

// ─── Manager principal ───────────────────────────────────────────────────────

/// Gestiona hasta 15 instancias simultáneas del backend Python.
/// Startup en lotes paralelos para no saturar el sistema.
class MultiBackendManager {
  final List<BackendManager> _managers = [];
  final List<CameraSlotConfig> _configs = [];

  List<BackendManager> get managers => List.unmodifiable(_managers);
  List<CameraSlotConfig> get configs => List.unmodifiable(_configs);
  int get count => _managers.length;
  List<int> get activePorts => _managers.map((m) => m.port).toList();

  // ── Persistencia ───────────────────────────────────────────────────────

  static Future<List<CameraSlotConfig>> loadSavedConfig() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw == null) return _defaultConfig();
      final list = (jsonDecode(raw) as List)
          .map((e) => CameraSlotConfig.fromJson(e as Map<String, dynamic>))
          .toList();
      return list.isEmpty ? _defaultConfig() : list;
    } catch (_) {
      return _defaultConfig();
    }
  }

  static Future<void> saveConfig(List<CameraSlotConfig> configs) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
        _prefsKey, jsonEncode(configs.map((c) => c.toJson()).toList()));
  }

  static List<CameraSlotConfig> _defaultConfig() => [
        const CameraSlotConfig(
          port: kBasePort,
          cameraIndex: 0,
          label: 'Recepción Principal',
          zone: 'Recepción / Admisión',
          priority: CameraPriority.normal,
          frameSkip: 5,
          snapshotQuality: 75,
        ),
      ];

  /// Genera preset hospitalario con N cámaras distribuidas en zonas típicas.
  static List<CameraSlotConfig> hospitalPreset(int count) {
    final zones = [
      'Urgencias',            // Alta prioridad
      'UCI / Cuidados Intensivos',
      'Recepción / Admisión',
      'Consultas Externas',
    ];
    return List.generate(
      count.clamp(1, kMaxCameras),
      (i) => CameraSlotConfig.autoForZone(
        port: kBasePort + i,
        cameraIndex: i,
        zone: zones[i % zones.length],
        label: 'Cámara ${i + 1} — ${zones[i % zones.length]}',
      ),
    );
  }

  // ── Startup con lotes paralelos ────────────────────────────────────────

  /// Inicia todas las instancias en lotes de [_startBatchSize] en paralelo.
  /// [onProgress] se llama cada vez que cambia el estado de inicio.
  Future<List<String?>> startAll(
    List<CameraSlotConfig> configs,
    String companyId, {
    void Function(StartupProgress)? onProgress,
  }) async {
    await stopAll();
    _configs.clear();
    _managers.clear();

    final n = configs.length;
    final errors = List<String?>.filled(n, null);

    // Pre-crear managers
    for (final cfg in configs) {
      _managers.add(BackendManager(
        port: cfg.port,
        cameraIndex: cfg.cameraIndex,
        frameSkip: cfg.frameSkip,
        snapshotQuality: cfg.snapshotQuality,
      ));
      _configs.add(cfg);
    }

    onProgress?.call(StartupProgress(
      total: n,
      currentAction: 'Lanzando backends...',
    ));

    int launched = 0;
    // Lanzar en lotes de _startBatchSize
    for (var start = 0; start < n; start += _startBatchSize) {
      final end = (start + _startBatchSize).clamp(0, n);
      final batch = List.generate(end - start, (i) => start + i);

      // Lanzar el lote en paralelo
      await Future.wait(batch.map((i) async {
        errors[i] = await _managers[i].start(companyId: companyId);
        if (errors[i] != null) {
          debugPrint('[MultiBackend] Error port=${configs[i].port}: ${errors[i]}');
        }
      }));

      launched += batch.length;
      onProgress?.call(StartupProgress(
        total: n,
        launched: launched,
        currentAction: 'Lanzados $launched/$n backends...',
      ));

      // Pequeña pausa entre lotes para que el sistema no se sature
      if (end < n) await Future.delayed(const Duration(milliseconds: 500));
    }

    return errors;
  }

  /// Espera a que todas las instancias respondan /api/health en paralelo.
  Future<List<bool>> waitForAll({
    int maxRetries = 40,
    void Function(StartupProgress)? onProgress,
  }) async {
    final n = _managers.length;
    final ready = List<bool>.filled(n, false);
    int doneCount = 0;

    // Verificar en paralelo con Future.wait
    await Future.wait(
      List.generate(n, (i) async {
        final port = _managers[i].port;
        for (var attempt = 0; attempt < maxRetries; attempt++) {
          if (_managers[i].didCrash) break;
          try {
            final r = await http
                .get(Uri.parse('http://127.0.0.1:$port/api/health'))
                .timeout(const Duration(seconds: 2));
            if (r.statusCode == 200) {
              final dir = _managers[i].backendDir;
              if (dir != null) {
                await BackendClient.forPort(port)
                    .loadToken(dir, instanceId: _managers[i].instanceId);
              }
              ready[i] = true;
              break;
            }
          } catch (_) {}
          await Future.delayed(const Duration(milliseconds: 500));
        }
        doneCount++;
        onProgress?.call(StartupProgress(
          total: n,
          launched: n,
          ready: ready.where((r) => r).length,
          failed: doneCount - ready.where((r) => r).length,
          currentAction: 'Verificando $doneCount/$n...',
        ));
      }),
    );

    return ready;
  }

  // ── Shutdown ───────────────────────────────────────────────────────────

  Future<void> stopAll() async {
    final ports = activePorts;
    // Enviar shutdown en paralelo
    await Future.wait(_managers.map((m) => m.kill()));
    _managers.clear();
    _configs.clear();
    BackendManager.killAll(ports: ports.isEmpty ? [kBasePort] : ports);
  }

  // ── Estimación de recursos ─────────────────────────────────────────────

  /// Estimación de uso de RAM en MB para N instancias.
  static int estimatedRamMb(int count) => count * 280;

  /// Estimación de uso de CPU (núcleos equivalentes, aprox).
  static double estimatedCpuCores(List<CameraSlotConfig> configs) {
    double total = 0;
    for (final cfg in configs) {
      // A menor frameSkip, más CPU usa
      total += 0.25 + (3.0 / cfg.frameSkip.clamp(1, 10));
    }
    return total;
  }
}
