import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

const _supabaseUrl = 'https://gumkpfyrgctrgemqihxl.supabase.co';
const _supabaseAnonKey =
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    '.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd1bWtwZnlyZ2N0cmdlbXFpaHhsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAxODA4ODgsImV4cCI6MjA5NTc1Njg4OH0'
    '.vJBevQSdheMMiCIzjdRonESpB8sdjqqX5IaPKnMRC4Y';

const _prefCompanyId   = 'company_id';
const _prefCompanyName = 'company_name';

class AuthResult {
  final bool valid;
  final String? companyId;
  final String? companyName;
  final String? error;

  const AuthResult({
    required this.valid,
    this.companyId,
    this.companyName,
    this.error,
  });
}

class AuthService {
  static final AuthService instance = AuthService._();
  AuthService._();

  String? _companyId;
  String? _companyName;

  String? get companyId   => _companyId;
  String? get companyName => _companyName;
  bool    get isLoggedIn  => _companyId != null && _companyId!.isNotEmpty;

  /// Carga la sesión guardada en disco. Llamar al arrancar la app.
  Future<bool> loadSession() async {
    final prefs = await SharedPreferences.getInstance();
    _companyId   = prefs.getString(_prefCompanyId);
    _companyName = prefs.getString(_prefCompanyName);
    return isLoggedIn;
  }

  /// Llama a validate_company_login en Supabase (anon key, sin auth).
  /// Valida que la empresa exista y tenga licencia activa.
  Future<AuthResult> validateAndLogin(String rawInput) async {
    final input = rawInput.trim();
    if (input.isEmpty) {
      return const AuthResult(valid: false, error: 'Ingresa el ID de empresa');
    }

    // Validar formato UUID básico
    final uuidRegex = RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
      caseSensitive: false,
    );
    if (!uuidRegex.hasMatch(input)) {
      return const AuthResult(
        valid: false,
        error: 'Formato inválido. El ID debe tener el formato:\nxxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
      );
    }

    try {
      final uri = Uri.parse('$_supabaseUrl/rest/v1/rpc/validate_company_login');
      final resp = await http
          .post(
            uri,
            headers: {
              'apikey':        _supabaseAnonKey,
              'Authorization': 'Bearer $_supabaseAnonKey',
              'Content-Type':  'application/json',
            },
            body: jsonEncode({'p_company_id': input}),
          )
          .timeout(const Duration(seconds: 10));

      if (!resp.ok) {
        return AuthResult(
          valid: false,
          error: 'Error de conexión (${resp.statusCode}). Verifica tu internet.',
        );
      }

      final data = jsonDecode(resp.body) as Map<String, dynamic>;

      if (data['valid'] != true) {
        return AuthResult(valid: false, error: data['error'] as String? ?? 'Login inválido');
      }

      final companyId   = data['company_id']   as String;
      final companyName = data['company_name'] as String;

      await _saveSession(companyId, companyName);
      return AuthResult(valid: true, companyId: companyId, companyName: companyName);
    } on Exception catch (e) {
      return AuthResult(valid: false, error: 'Sin conexión a Supabase: $e');
    }
  }

  Future<void> _saveSession(String companyId, String companyName) async {
    _companyId   = companyId;
    _companyName = companyName;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefCompanyId,   companyId);
    await prefs.setString(_prefCompanyName, companyName);
  }

  Future<void> logout() async {
    _companyId   = null;
    _companyName = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_prefCompanyId);
    await prefs.remove(_prefCompanyName);
  }
}

extension on http.Response {
  bool get ok => statusCode >= 200 && statusCode < 300;
}
