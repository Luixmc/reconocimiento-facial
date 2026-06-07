import 'dart:convert';

import 'package:http/http.dart' as http;

const _supabaseUrl = 'https://gumkpfyrgctrgemqihxl.supabase.co';
const _supabaseAnonKey =
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    '.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd1bWtwZnlyZ2N0cmdlbXFpaHhsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAxODA4ODgsImV4cCI6MjA5NTc1Njg4OH0'
    '.vJBevQSdheMMiCIzjdRonESpB8sdjqqX5IaPKnMRC4Y';

/// Resultado de un intento de login de operador (admin/superadmin).
class OperatorAuthResult {
  final bool valid;
  final String? operatorId;
  final String? displayName;
  final String? role;
  final String? error;

  const OperatorAuthResult({
    required this.valid,
    this.operatorId,
    this.displayName,
    this.role,
    this.error,
  });
}

/// Sesión de operador admin actualmente autenticado (solo en memoria —
/// se cierra al salir del panel, no persiste entre reinicios de la app).
class OperatorSession {
  final String operatorId;
  final String displayName;
  final String role;

  const OperatorSession({
    required this.operatorId,
    required this.displayName,
    required this.role,
  });
}

/// Login de administradores (company_operators) vía RPC validate_operator_login.
/// Independiente de AuthService (que maneja la sesión de la EMPRESA, no del operador).
class OperatorAuthService {
  static final OperatorAuthService instance = OperatorAuthService._();
  OperatorAuthService._();

  OperatorSession? _session;
  OperatorSession? get session => _session;
  bool get isLoggedIn => _session != null;

  /// Valida email + contraseña del operador contra la empresa indicada.
  /// Solo permite continuar si el rol es 'admin' o 'superadmin'.
  Future<OperatorAuthResult> login({
    required String companyId,
    required String email,
    required String password,
  }) async {
    final trimmedEmail = email.trim();
    final trimmedPassword = password.trim();
    if (trimmedEmail.isEmpty || trimmedPassword.isEmpty) {
      return const OperatorAuthResult(
        valid: false,
        error: 'Ingresa correo y contraseña',
      );
    }

    try {
      final uri = Uri.parse('$_supabaseUrl/rest/v1/rpc/validate_operator_login');
      final resp = await http
          .post(
            uri,
            headers: {
              'apikey':        _supabaseAnonKey,
              'Authorization': 'Bearer $_supabaseAnonKey',
              'Content-Type':  'application/json',
            },
            body: jsonEncode({
              'p_company_id': companyId,
              'p_email':      trimmedEmail,
              'p_password':   trimmedPassword,
            }),
          )
          .timeout(const Duration(seconds: 10));

      if (!resp.ok) {
        return OperatorAuthResult(
          valid: false,
          error: 'Error de conexión (${resp.statusCode}). Verifica tu internet.',
        );
      }

      final rows = jsonDecode(resp.body) as List<dynamic>;
      if (rows.isEmpty) {
        return const OperatorAuthResult(valid: false, error: 'Usuario no encontrado');
      }

      final row = rows.first as Map<String, dynamic>;
      if (row['valid'] != true) {
        return OperatorAuthResult(
          valid: false,
          error: row['error'] as String? ?? 'Credenciales inválidas',
        );
      }

      final operatorId  = row['operator_id']  as String;
      final displayName = row['display_name'] as String;
      final role        = row['role']         as String;

      _session = OperatorSession(operatorId: operatorId, displayName: displayName, role: role);
      return OperatorAuthResult(
        valid: true,
        operatorId: operatorId,
        displayName: displayName,
        role: role,
      );
    } on Exception catch (e) {
      return OperatorAuthResult(valid: false, error: 'Sin conexión a Supabase: $e');
    }
  }

  void logout() {
    _session = null;
  }
}

extension on http.Response {
  bool get ok => statusCode >= 200 && statusCode < 300;
}
