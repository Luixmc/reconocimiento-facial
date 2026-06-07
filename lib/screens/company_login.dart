import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/auth_service.dart';

class CompanyLoginScreen extends StatefulWidget {
  final void Function(String companyId, String companyName) onLoginSuccess;

  const CompanyLoginScreen({super.key, required this.onLoginSuccess});

  @override
  State<CompanyLoginScreen> createState() => _CompanyLoginScreenState();
}

class _CompanyLoginScreenState extends State<CompanyLoginScreen> {
  final _controller = TextEditingController();
  final _focusNode  = FocusNode();

  bool    _loading = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() { _loading = true; _error = null; });

    final result = await AuthService.instance.validateAndLogin(_controller.text);

    if (!mounted) return;
    setState(() => _loading = false);

    if (result.valid) {
      widget.onLoginSuccess(result.companyId!, result.companyName!);
    } else {
      setState(() => _error = result.error);
    }
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    if (data?.text != null) {
      _controller.text = data!.text!.trim();
      _controller.selection = TextSelection.fromPosition(
        TextPosition(offset: _controller.text.length),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0D1117),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(32),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Logo / ícono
                Container(
                  width: 80,
                  height: 80,
                  decoration: BoxDecoration(
                    color: const Color(0xFF1A237E).withValues(alpha: 0.3),
                    shape: BoxShape.circle,
                    border: Border.all(color: Colors.cyanAccent.withValues(alpha: 0.5), width: 2),
                  ),
                  child: const Icon(Icons.fingerprint, size: 44, color: Colors.cyanAccent),
                ),
                const SizedBox(height: 24),

                const Text(
                  'BioFace',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 28,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 2,
                  ),
                ),
                const SizedBox(height: 6),
                const Text(
                  'Sistema de Reconocimiento Facial',
                  style: TextStyle(color: Colors.white54, fontSize: 13),
                ),
                const SizedBox(height: 40),

                // Tarjeta de login
                Container(
                  padding: const EdgeInsets.all(28),
                  decoration: BoxDecoration(
                    color: const Color(0xFF161B22),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: Colors.white12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const Text(
                        'Acceso de Empresa',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 6),
                      const Text(
                        'Ingresa el ID único de tu empresa para continuar.',
                        style: TextStyle(color: Colors.white54, fontSize: 13),
                      ),
                      const SizedBox(height: 24),

                      // Campo ID
                      TextField(
                        controller: _controller,
                        focusNode: _focusNode,
                        enabled: !_loading,
                        style: const TextStyle(
                          color: Colors.white,
                          fontFamily: 'monospace',
                          fontSize: 14,
                          letterSpacing: 1,
                        ),
                        decoration: InputDecoration(
                          labelText: 'ID de Empresa',
                          labelStyle: const TextStyle(color: Colors.white54),
                          hintText: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
                          hintStyle: TextStyle(
                            color: Colors.white24,
                            fontFamily: 'monospace',
                            fontSize: 13,
                          ),
                          filled: true,
                          fillColor: const Color(0xFF0D1117),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(10),
                            borderSide: const BorderSide(color: Colors.white12),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(10),
                            borderSide: const BorderSide(color: Colors.white12),
                          ),
                          focusedBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(10),
                            borderSide: const BorderSide(color: Colors.cyanAccent, width: 1.5),
                          ),
                          errorBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(10),
                            borderSide: const BorderSide(color: Colors.redAccent),
                          ),
                          suffixIcon: IconButton(
                            icon: const Icon(Icons.content_paste, color: Colors.white38, size: 20),
                            tooltip: 'Pegar desde portapapeles',
                            onPressed: _loading ? null : _paste,
                          ),
                          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                        ),
                        onSubmitted: (_) => _loading ? null : _submit(),
                      ),

                      // Error
                      if (_error != null) ...[
                        const SizedBox(height: 12),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                          decoration: BoxDecoration(
                            color: Colors.redAccent.withValues(alpha: 0.1),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(color: Colors.redAccent.withValues(alpha: 0.4)),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Icon(Icons.error_outline, color: Colors.redAccent, size: 18),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Text(
                                  _error!,
                                  style: const TextStyle(color: Colors.redAccent, fontSize: 13),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],

                      const SizedBox(height: 20),

                      // Botón ingresar
                      SizedBox(
                        height: 48,
                        child: ElevatedButton(
                          onPressed: _loading ? null : _submit,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.cyanAccent,
                            foregroundColor: Colors.black,
                            disabledBackgroundColor: Colors.cyanAccent.withValues(alpha: 0.3),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(10),
                            ),
                          ),
                          child: _loading
                              ? const SizedBox(
                                  width: 22,
                                  height: 22,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2.5,
                                    color: Colors.black54,
                                  ),
                                )
                              : const Text(
                                  'Ingresar',
                                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                                ),
                        ),
                      ),
                    ],
                  ),
                ),

                const SizedBox(height: 24),
                const Text(
                  'El ID de empresa lo proporciona el administrador del sistema.',
                  style: TextStyle(color: Colors.white24, fontSize: 11),
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
