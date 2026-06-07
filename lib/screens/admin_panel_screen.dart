import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../services/backend_client.dart';
import '../services/operator_auth_service.dart';

const _backendHost = '127.0.0.1';
const _backendPort = 5050;

/// Panel de administración: permite a operadores admin/superadmin
/// registrar personas nuevas y enrolar (capturar) sus rostros.
class AdminPanelScreen extends StatefulWidget {
  final String companyName;
  final OperatorSession operatorSession;

  const AdminPanelScreen({
    super.key,
    required this.companyName,
    required this.operatorSession,
  });

  @override
  State<AdminPanelScreen> createState() => _AdminPanelScreenState();
}

class _AdminPanelScreenState extends State<AdminPanelScreen> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _persons = [];

  @override
  void initState() {
    super.initState();
    _loadPersons();
  }

  Future<void> _loadPersons() async {
    setState(() { _loading = true; _error = null; });
    try {
      final resp = await BackendClient.instance.get('/api/persons');
      if (resp.statusCode == 200) {
        final list = jsonDecode(resp.body) as List<dynamic>;
        setState(() {
          _persons = list.cast<Map<String, dynamic>>();
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'No se pudo cargar la lista de personas (${resp.statusCode})';
          _loading = false;
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Sin conexión con el backend: $e';
        _loading = false;
      });
    }
  }

  Future<void> _createPerson() async {
    final nameController = TextEditingController();
    final docController  = TextEditingController();
    final posController  = TextEditingController();

    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF161B22),
        title: const Text('Nueva persona', style: TextStyle(color: Colors.white)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: nameController,
              autofocus: true,
              style: const TextStyle(color: Colors.white),
              decoration: _dialogFieldDecoration('Nombre completo *'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: docController,
              style: const TextStyle(color: Colors.white),
              decoration: _dialogFieldDecoration('Documento (opcional)'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: posController,
              style: const TextStyle(color: Colors.white),
              decoration: _dialogFieldDecoration('Cargo / posición (opcional)'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          ElevatedButton(
            onPressed: () {
              if (nameController.text.trim().isEmpty) return;
              Navigator.pop(ctx, true);
            },
            style: ElevatedButton.styleFrom(backgroundColor: Colors.amberAccent, foregroundColor: Colors.black),
            child: const Text('Crear'),
          ),
        ],
      ),
    );

    if (created != true) return;
    final fullName = nameController.text.trim();
    if (fullName.isEmpty) return;

    try {
      final resp = await BackendClient.instance.post(
        '/api/admin/persons',
        body: jsonEncode({
          'full_name': fullName,
          'document_number': docController.text.trim(),
          'position': posController.text.trim(),
          'operator_id': widget.operatorSession.operatorId,
        }),
      );
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      if (!mounted) return;
      if (resp.statusCode == 200 && data['ok'] == true) {
        _showSnack('Persona "$fullName" registrada. Ahora enrola su rostro.', success: true);
        await _loadPersons();
      } else {
        _showSnack(data['error'] as String? ?? 'No se pudo crear la persona', success: false);
      }
    } catch (e) {
      if (!mounted) return;
      _showSnack('Error de conexión: $e', success: false);
    }
  }

  void _showSnack(String message, {required bool success}) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(message),
      backgroundColor: success ? Colors.green.shade700 : Colors.red.shade700,
      behavior: SnackBarBehavior.floating,
    ));
  }

  InputDecoration _dialogFieldDecoration(String label) => InputDecoration(
        labelText: label,
        labelStyle: const TextStyle(color: Colors.white54),
        filled: true,
        fillColor: const Color(0xFF0D1117),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: const BorderSide(color: Colors.white12)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: const BorderSide(color: Colors.white12)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(8), borderSide: const BorderSide(color: Colors.amberAccent)),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      );

  Future<void> _openEnrollment(Map<String, dynamic> person) async {
    await Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => FaceEnrollmentScreen(
        personId: person['id'] as String,
        personName: person['full_name'] as String? ?? 'Persona',
      ),
    ));
    _loadPersons(); // refrescar conteo de embeddings al volver
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0D1117),
      appBar: AppBar(
        backgroundColor: const Color(0xFF1A237E),
        elevation: 0,
        title: const Text('Panel de Administración'),
        actions: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Center(
              child: Tooltip(
                message: '${widget.operatorSession.displayName} (${widget.operatorSession.role})',
                child: Row(
                  children: [
                    const Icon(Icons.account_circle_outlined, size: 18, color: Colors.white70),
                    const SizedBox(width: 6),
                    Text(widget.operatorSession.displayName,
                        style: const TextStyle(fontSize: 13, color: Colors.white70)),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _createPerson,
        backgroundColor: Colors.amberAccent,
        foregroundColor: Colors.black,
        icon: const Icon(Icons.person_add_alt_1),
        label: const Text('Nueva persona', style: TextStyle(fontWeight: FontWeight.bold)),
      ),
      body: RefreshIndicator(
        onRefresh: _loadPersons,
        color: Colors.amberAccent,
        backgroundColor: const Color(0xFF161B22),
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: Colors.amberAccent));
    }
    if (_error != null) {
      return ListView(
        children: [
          const SizedBox(height: 80),
          Icon(Icons.cloud_off, size: 56, color: Colors.white24),
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white54)),
          ),
          const SizedBox(height: 16),
          Center(
            child: TextButton.icon(
              onPressed: _loadPersons,
              icon: const Icon(Icons.refresh, color: Colors.amberAccent),
              label: const Text('Reintentar', style: TextStyle(color: Colors.amberAccent)),
            ),
          ),
        ],
      );
    }
    if (_persons.isEmpty) {
      return ListView(
        children: const [
          SizedBox(height: 80),
          Icon(Icons.people_outline, size: 56, color: Colors.white24),
          SizedBox(height: 16),
          Center(
            child: Text('No hay personas registradas todavía.\nUsa "Nueva persona" para crear la primera.',
                textAlign: TextAlign.center, style: TextStyle(color: Colors.white54)),
          ),
        ],
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
      itemCount: _persons.length,
      separatorBuilder: (_, _) => const SizedBox(height: 10),
      itemBuilder: (context, i) {
        final p = _persons[i];
        final embeddingCount = (p['embedding_count'] as num?)?.toInt() ?? 0;
        final hasFace = embeddingCount > 0;
        final fullName = p['full_name'] as String? ?? 'Sin nombre';
        final document = p['document_number'] as String?;
        final position = p['position'] as String?;

        return Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: const Color(0xFF161B22),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.white12),
          ),
          child: Row(
            children: [
              CircleAvatar(
                radius: 24,
                backgroundColor: hasFace
                    ? Colors.greenAccent.withValues(alpha: 0.15)
                    : Colors.orangeAccent.withValues(alpha: 0.15),
                child: Icon(
                  hasFace ? Icons.verified_user_outlined : Icons.face_retouching_natural,
                  color: hasFace ? Colors.greenAccent : Colors.orangeAccent,
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(fullName, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 15)),
                    const SizedBox(height: 3),
                    Text(
                      [
                        if (document != null && document.isNotEmpty) 'Doc: $document',
                        if (position != null && position.isNotEmpty) position,
                      ].join('  •  '),
                      style: const TextStyle(color: Colors.white54, fontSize: 12),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      hasFace
                          ? '$embeddingCount rostro(s) enrolado(s)'
                          : 'Sin rostro enrolado — no podrá ser reconocida',
                      style: TextStyle(
                        color: hasFace ? Colors.greenAccent : Colors.orangeAccent,
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              ElevatedButton.icon(
                onPressed: () => _openEnrollment(p),
                style: ElevatedButton.styleFrom(
                  backgroundColor: hasFace ? const Color(0xFF21262D) : Colors.amberAccent,
                  foregroundColor: hasFace ? Colors.white : Colors.black,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                ),
                icon: Icon(hasFace ? Icons.add_a_photo_outlined : Icons.camera_alt, size: 18),
                label: Text(hasFace ? 'Agregar rostro' : 'Enrolar rostro'),
              ),
            ],
          ),
        );
      },
    );
  }
}

// ─── Captura de rostro para enrollment ──────────────────────────────────────

class FaceEnrollmentScreen extends StatefulWidget {
  final String personId;
  final String personName;

  const FaceEnrollmentScreen({
    super.key,
    required this.personId,
    required this.personName,
  });

  @override
  State<FaceEnrollmentScreen> createState() => _FaceEnrollmentScreenState();
}

class _FaceEnrollmentScreenState extends State<FaceEnrollmentScreen> {
  Timer? _frameTimer;
  Uint8List? _currentFrame;
  bool _capturing = false;
  int _capturedCount = 0;
  String? _lastMessage;
  bool _lastSuccess = true;

  @override
  void initState() {
    super.initState();
    _frameTimer = Timer.periodic(const Duration(milliseconds: 120), (_) => _fetchFrame());
    _fetchFrame();
  }

  @override
  void dispose() {
    _frameTimer?.cancel();
    super.dispose();
  }

  Future<void> _fetchFrame() async {
    try {
      final resp = await http
          .get(Uri.parse('http://$_backendHost:$_backendPort/api/snapshot'))
          .timeout(const Duration(seconds: 1));
      if (resp.statusCode == 200 && resp.bodyBytes.isNotEmpty && mounted) {
        setState(() => _currentFrame = resp.bodyBytes);
      }
    } catch (_) {}
  }

  Future<void> _capture() async {
    final frame = _currentFrame;
    if (frame == null || _capturing) return;

    setState(() { _capturing = true; _lastMessage = null; });
    try {
      final resp = await BackendClient.instance.postMultipart(
        '/api/enroll',
        fields: {'person_id': widget.personId},
        fileField: 'image',
        fileBytes: frame,
        filename: 'enroll.jpg',
      );
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      if (!mounted) return;

      if (resp.statusCode == 200 && data['ok'] == true) {
        setState(() {
          _capturing = false;
          _capturedCount++;
          _lastSuccess = true;
          _lastMessage = '¡Captura $_capturedCount guardada! '
              '(confianza: ${((data['confidence'] as num?)?.toDouble() ?? 0).toStringAsFixed(2)})';
        });
      } else {
        setState(() {
          _capturing = false;
          _lastSuccess = false;
          _lastMessage = data['error'] as String? ?? 'No se pudo procesar la captura';
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _capturing = false;
        _lastSuccess = false;
        _lastMessage = 'Error de conexión: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: const Color(0xFF1A237E),
        elevation: 0,
        title: Text('Enrolar rostro — ${widget.personName}'),
      ),
      body: Column(
        children: [
          Expanded(
            child: Stack(
              fit: StackFit.expand,
              children: [
                _currentFrame != null
                    ? Image.memory(_currentFrame!, fit: BoxFit.contain, gaplessPlayback: true)
                    : const Center(child: CircularProgressIndicator(color: Colors.amberAccent)),

                // Guía de encuadre
                Center(
                  child: Container(
                    width: 260,
                    height: 320,
                    decoration: BoxDecoration(
                      border: Border.all(color: Colors.amberAccent.withValues(alpha: 0.7), width: 3),
                      borderRadius: BorderRadius.circular(140),
                    ),
                  ),
                ),

                Positioned(
                  top: 16, left: 16, right: 16,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    decoration: BoxDecoration(
                      color: Colors.black54,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Text(
                      'Centra el rostro dentro del óvalo, con buena luz y mirando a la cámara. '
                      'Captura 3-5 fotos desde ángulos ligeramente distintos para mejorar el reconocimiento.',
                      style: TextStyle(color: Colors.white, fontSize: 13),
                      textAlign: TextAlign.center,
                    ),
                  ),
                ),

                if (_lastMessage != null)
                  Positioned(
                    bottom: 16, left: 16, right: 16,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      decoration: BoxDecoration(
                        color: (_lastSuccess ? Colors.green.shade700 : Colors.red.shade700).withValues(alpha: 0.9),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Row(
                        children: [
                          Icon(_lastSuccess ? Icons.check_circle_outline : Icons.error_outline, color: Colors.white),
                          const SizedBox(width: 10),
                          Expanded(child: Text(_lastMessage!, style: const TextStyle(color: Colors.white))),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
          Container(
            color: const Color(0xFF161B22),
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Capturas guardadas: $_capturedCount',
                          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                      const Text('Recomendado: al menos 3 capturas',
                          style: TextStyle(color: Colors.white54, fontSize: 12)),
                    ],
                  ),
                ),
                ElevatedButton.icon(
                  onPressed: (_currentFrame == null || _capturing) ? null : _capture,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.amberAccent,
                    foregroundColor: Colors.black,
                    disabledBackgroundColor: Colors.amberAccent.withValues(alpha: 0.3),
                    padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  ),
                  icon: _capturing
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black54))
                      : const Icon(Icons.camera_alt),
                  label: Text(_capturing ? 'Procesando...' : 'Capturar rostro',
                      style: const TextStyle(fontWeight: FontWeight.bold)),
                ),
                const SizedBox(width: 12),
                OutlinedButton(
                  onPressed: () => Navigator.of(context).pop(),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white70,
                    side: const BorderSide(color: Colors.white24),
                    padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  ),
                  child: const Text('Listo'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
