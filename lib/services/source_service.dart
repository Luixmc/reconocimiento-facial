import 'dart:convert';

import 'package:http/http.dart' as http;

// ────────────────────────────────────────────────────────────────────────────
// Modelos
// ────────────────────────────────────────────────────────────────────────────

/// Una fuente de video disponible (USB o DroidCam).
class SourceInfo {
  final int index;
  final String name;
  final String backend;
  final String? source;
  final String? url;

  const SourceInfo({
    required this.index,
    required this.name,
    this.backend = '',
    this.source,
    this.url,
  });

  factory SourceInfo.fromJson(Map<String, dynamic> json) => SourceInfo(
        index: json['index'] as int? ?? -1,
        name: json['name'] as String? ?? 'Desconocida',
        backend: json['backend'] as String? ?? '',
        source: json['source'] as String?,
        url: json['url'] as String?,
      );

  Map<String, dynamic> toJson() => {
        'index': index,
        'name': name,
        'backend': backend,
        'source': source,
        'url': url,
      };

  bool get isDroidcam => source == 'droidcam' || index == -1;
}

/// Estado actual de la fuente de video activa.
class SourceStatus {
  final bool active;
  final String sourceType;
  final String sourceId;
  final String? sourceUrl;
  final double fps;
  final String? error;
  final String resolution;
  final String configSourceType;
  final String? configManualUrl;

  const SourceStatus({
    this.active = false,
    this.sourceType = '',
    this.sourceId = '',
    this.sourceUrl,
    this.fps = 0,
    this.error,
    this.resolution = '',
    this.configSourceType = 'usb',
    this.configManualUrl,
  });

  factory SourceStatus.fromJson(Map<String, dynamic> json) => SourceStatus(
        active: json['active'] as bool? ?? false,
        sourceType: json['source_type'] as String? ?? '',
        sourceId: json['source_id'] as String? ?? '',
        sourceUrl: json['source_url'] as String?,
        fps: (json['fps'] as num?)?.toDouble() ?? 0,
        error: json['error'] as String?,
        resolution: json['resolution'] as String? ?? '',
        configSourceType: json['config_source_type'] as String? ?? 'usb',
        configManualUrl: json['config_manual_url'] as String?,
      );

  bool get isDroidcam => sourceType == 'droidcam';

  String get displayName =>
      isDroidcam ? 'DroidCam ($sourceId)' : 'Cámara $sourceId';
}

/// Configuración para enviar a POST /api/source/select.
class SourceConfig {
  final String sourceType;
  final String? manualUrl;
  final int usbIndex;
  final bool autoDiscover;

  const SourceConfig({
    this.sourceType = 'usb',
    this.manualUrl,
    this.usbIndex = 0,
    this.autoDiscover = true,
  });

  factory SourceConfig.usb({int index = 0}) => SourceConfig(
        sourceType: 'usb',
        usbIndex: index,
        autoDiscover: false,
      );

  factory SourceConfig.droidcam({String? url, bool autoDiscover = true}) =>
      SourceConfig(
        sourceType: 'droidcam',
        manualUrl: url,
        autoDiscover: autoDiscover,
      );

  factory SourceConfig.manual(String url) => SourceConfig(
        sourceType: 'manual',
        manualUrl: url,
        autoDiscover: false,
      );

  Map<String, dynamic> toJson() => {
        'source_type': sourceType,
        if (manualUrl != null) 'manual_url': manualUrl,
        'usb_index': usbIndex,
        'auto_discover': autoDiscover,
      };
}

/// Resultado de seleccionar una fuente.
class SourceSelectResult {
  final bool ok;
  final String sourceType;
  final String sourceId;
  final String? sourceUrl;
  final String? error;
  final bool fallback;
  final String? fallbackSourceType;
  final String? fallbackSourceId;

  const SourceSelectResult({
    required this.ok,
    this.sourceType = '',
    this.sourceId = '',
    this.sourceUrl,
    this.error,
    this.fallback = false,
    this.fallbackSourceType,
    this.fallbackSourceId,
  });

  factory SourceSelectResult.fromJson(Map<String, dynamic> json) =>
      SourceSelectResult(
        ok: json['ok'] as bool? ?? false,
        sourceType: json['source_type'] as String? ?? '',
        sourceId: json['source_id'] as String? ?? '',
        sourceUrl: json['source_url'] as String?,
        error: json['error'] as String?,
        fallback: json['fallback'] as bool? ?? false,
        fallbackSourceType: json['fallback_source_type'] as String?,
        fallbackSourceId: json['fallback_source_id'] as String?,
      );
}

/// Resultado del escaneo de DroidCam.
class DroidcamScanResult {
  final bool ok;
  final String? url;
  final String? ip;
  final String? message;

  const DroidcamScanResult({
    this.ok = false,
    this.url,
    this.ip,
    this.message,
  });

  factory DroidcamScanResult.fromJson(Map<String, dynamic> json) =>
      DroidcamScanResult(
        ok: json['ok'] as bool? ?? false,
        url: json['url'] as String?,
        ip: json['ip'] as String?,
        message: json['message'] as String?,
      );
}

// ────────────────────────────────────────────────────────────────────────────
// SourceService — API client para /api/source/*
// ────────────────────────────────────────────────────────────────────────────

/// Servicio que consume los endpoints /api/source/* del backend Python.
class SourceService {
  final String host;
  final int port;
  final Duration timeout;

  const SourceService({
    this.host = '127.0.0.1',
    this.port = 5050,
    this.timeout = const Duration(seconds: 5),
  });

  Uri _uri(String path) => Uri.parse('http://$host:$port$path');

  Map<String, String> get _jsonHeaders => {
        'Content-Type': 'application/json',
      };

  // ── GET /api/source/status ───────────────────────────────────────────

  /// Estado actual de la fuente de video activa.
  Future<SourceStatus> getStatus() async {
    try {
      final response = await http
          .get(_uri('/api/source/status'))
          .timeout(timeout);
      if (response.statusCode == 200) {
        final json = jsonDecode(response.body) as Map<String, dynamic>;
        return SourceStatus.fromJson(json);
      }
    } catch (e) {
      // Timeout o error de conexión
    }
    return const SourceStatus(error: 'No se pudo obtener el estado');
  }

  // ── GET /api/source/list ─────────────────────────────────────────────

  /// Lista todas las fuentes disponibles (USB + DroidCam).
  Future<List<SourceInfo>> listSources() async {
    try {
      final response = await http
          .get(_uri('/api/source/list'))
          .timeout(timeout);
      if (response.statusCode == 200) {
        final List<dynamic> jsonList = jsonDecode(response.body);
        return jsonList
            .cast<Map<String, dynamic>>()
            .map((j) => SourceInfo.fromJson(j))
            .toList();
      }
    } catch (e) {
      // Timeout o error de conexión
    }
    return [];
  }

  // ── POST /api/source/select ──────────────────────────────────────────

  /// Selecciona una fuente de video.
  ///
  /// Ejemplos:
  ///   ```dart
  ///   // USB por índice
  ///   sourceService.selectSource(SourceConfig.usb(index: 0));
  ///
  ///   // DroidCam con autodescubrimiento
  ///   sourceService.selectSource(SourceConfig.droidcam());
  ///
  ///   // URL manual (RTSP, HTTP, etc.)
  ///   sourceService.selectSource(SourceConfig.manual("rtsp://..."));
  ///   ```
  Future<SourceSelectResult> selectSource(SourceConfig config) async {
    try {
      final response = await http
          .post(
            _uri('/api/source/select'),
            headers: _jsonHeaders,
            body: jsonEncode(config.toJson()),
          )
          .timeout(const Duration(seconds: 10)); // más tiempo por DroidCam
      if (response.statusCode == 200) {
        final json = jsonDecode(response.body) as Map<String, dynamic>;
        return SourceSelectResult.fromJson(json);
      }
      final json = jsonDecode(response.body) as Map<String, dynamic>;
      return SourceSelectResult(
        ok: false,
        error: json['error'] as String? ?? 'Error HTTP ${response.statusCode}',
      );
    } catch (e) {
      return SourceSelectResult(
        ok: false,
        error: 'Error de conexión: $e',
      );
    }
  }

  // ── POST /api/source/scan-droidcam ───────────────────────────────────

  /// Escanea la red local en busca de DroidCam.
  Future<DroidcamScanResult> scanDroidcam() async {
    try {
      final response = await http
          .post(_uri('/api/source/scan-droidcam'))
          .timeout(const Duration(seconds: 30)); // escaneo de red lento
      if (response.statusCode == 200) {
        final json = jsonDecode(response.body) as Map<String, dynamic>;
        return DroidcamScanResult.fromJson(json);
      }
    } catch (e) {
      // Timeout o error de conexión
    }
    return const DroidcamScanResult(
      message: 'No se pudo escanear la red',
    );
  }

  // ── Conveniencia: escanear y seleccionar DroidCam ────────────────────

  /// Escanea la red, y si encuentra DroidCam, lo selecciona automáticamente.
  ///
  /// Retorna el resultado de la selección, o `null` si no se encontró.
  Future<SourceSelectResult?> scanAndSelectDroidcam() async {
    final scanResult = await scanDroidcam();
    if (!scanResult.ok || scanResult.url == null) {
      return null;
    }
    return selectSource(SourceConfig.droidcam(url: scanResult.url));
  }

  // ── Conveniencia: seleccionar por SourceInfo ──────────────────────────

  /// Selecciona una fuente a partir de un [SourceInfo].
  Future<SourceSelectResult> selectSourceInfo(SourceInfo info) async {
    if (info.isDroidcam) {
      return selectSource(SourceConfig.droidcam(url: info.url));
    }
    return selectSource(SourceConfig.usb(index: info.index));
  }
}
