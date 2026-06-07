import 'dart:io';

import 'package:http/http.dart' as http;

/// Cliente HTTP que inyecta X-Backend-Token en cada request.
/// Soporta múltiples instancias del backend en distintos puertos.
class BackendClient {
  final int port;
  String? _token;

  BackendClient._(this.port);

  static const Duration _timeout = Duration(seconds: 5);

  // Singleton para el puerto por defecto (5050)
  static final BackendClient instance = BackendClient._(5050);

  // Cache de instancias por puerto
  static final Map<int, BackendClient> _instances = {};

  /// Retorna (o crea) un cliente para el puerto dado.
  static BackendClient forPort(int port) {
    if (port == 5050) return instance;
    return _instances.putIfAbsent(port, () => BackendClient._(port));
  }

  String get _baseUrl => 'http://127.0.0.1:$port';

  /// Carga el token desde disco usando el instanceId (= puerto por defecto).
  Future<void> loadToken(String backendDir, {String? instanceId}) async {
    final id = instanceId ?? '$port';
    final file = File('$backendDir/backend_$id.token');
    try {
      if (await file.exists()) {
        _token = (await file.readAsString()).trim();
      }
    } catch (_) {}
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'X-Backend-Token': _token ?? '',
      };

  Uri _uri(String path) => Uri.parse('$_baseUrl$path');

  Future<http.Response> get(String path) =>
      http.get(_uri(path), headers: _headers).timeout(_timeout);

  Future<http.Response> post(String path, {Object? body}) =>
      http.post(_uri(path), headers: _headers, body: body).timeout(_timeout);

  /// POST multipart/form-data (p.ej. para subir una imagen de enrollment).
  Future<http.Response> postMultipart(
    String path, {
    Map<String, String> fields = const {},
    required String fileField,
    required List<int> fileBytes,
    required String filename,
  }) async {
    final request = http.MultipartRequest('POST', _uri(path))
      ..headers.addAll({'X-Backend-Token': _token ?? ''})
      ..fields.addAll(fields)
      ..files.add(http.MultipartFile.fromBytes(fileField, fileBytes, filename: filename));
    final streamed = await request.send().timeout(const Duration(seconds: 20));
    return http.Response.fromStream(streamed);
  }
}
