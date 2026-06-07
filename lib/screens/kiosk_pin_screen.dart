import 'package:flutter/material.dart';

import '../services/device_service.dart';

/// Diálogo de PIN para salir del modo kiosko.
/// Se abre con triple tap en el área de título.
class KioskPinDialog extends StatefulWidget {
  const KioskPinDialog({super.key});

  /// Muestra el diálogo y retorna true si el PIN fue correcto.
  static Future<bool> show(BuildContext context) async {
    final result = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => const KioskPinDialog(),
    );
    return result == true;
  }

  @override
  State<KioskPinDialog> createState() => _KioskPinDialogState();
}

class _KioskPinDialogState extends State<KioskPinDialog> {
  final _ctrl = TextEditingController();
  bool _error = false;
  bool _loading = false;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _validate() async {
    if (_loading) return;
    setState(() { _loading = true; _error = false; });
    final ok = await DeviceService.validatePin(_ctrl.text.trim());
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop(true);
    } else {
      setState(() { _error = true; _loading = false; _ctrl.clear(); });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: const Color(0xFF1E1E2E),
      title: const Row(
        children: [
          Icon(Icons.lock_outline, color: Colors.cyanAccent, size: 20),
          SizedBox(width: 8),
          Text('Acceso administrativo',
              style: TextStyle(color: Colors.white, fontSize: 16)),
        ],
      ),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            'Ingresa el PIN de administrador para salir del modo kiosko.',
            style: TextStyle(color: Colors.white54, fontSize: 13),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _ctrl,
            obscureText: true,
            autofocus: true,
            keyboardType: TextInputType.number,
            maxLength: 8,
            style: const TextStyle(color: Colors.white, fontSize: 20,
                letterSpacing: 6, fontWeight: FontWeight.bold),
            textAlign: TextAlign.center,
            decoration: InputDecoration(
              counterText: '',
              hintText: '● ● ● ●',
              hintStyle: const TextStyle(color: Colors.white24),
              filled: true,
              fillColor: const Color(0xFF0D1117),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(
                  color: _error ? Colors.redAccent : Colors.white12,
                ),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(
                  color: _error ? Colors.redAccent : Colors.white12,
                ),
              ),
              errorText: _error ? 'PIN incorrecto' : null,
            ),
            onSubmitted: (_) => _validate(),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Cancelar',
              style: TextStyle(color: Colors.white38)),
        ),
        ElevatedButton(
          onPressed: _loading ? null : _validate,
          style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF1A237E)),
          child: _loading
              ? const SizedBox(
                  width: 16, height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2,
                      color: Colors.white))
              : const Text('Confirmar'),
        ),
      ],
    );
  }
}
