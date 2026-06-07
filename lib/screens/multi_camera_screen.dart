import 'dart:async';
import 'dart:convert';
import 'dart:math' show pow;
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../services/backend_client.dart';
import '../services/backend_manager.dart';
import '../services/multi_backend_manager.dart';

// ═══════════════════════════════════════════════════════════════════════════
// PANTALLA DE CONFIGURACIÓN
// ═══════════════════════════════════════════════════════════════════════════

class MultiCameraSetupScreen extends StatefulWidget {
  final String companyId;
  final String companyName;

  const MultiCameraSetupScreen({
    super.key,
    required this.companyId,
    required this.companyName,
  });

  @override
  State<MultiCameraSetupScreen> createState() => _MultiCameraSetupScreenState();
}

class _MultiCameraSetupScreenState extends State<MultiCameraSetupScreen> {
  List<CameraSlotConfig> _configs = [];
  bool _loading = true;
  bool _showResourceWarning = false;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  Future<void> _loadConfig() async {
    final saved = await MultiBackendManager.loadSavedConfig();
    setState(() {
      _configs = saved;
      _loading = false;
      _showResourceWarning = saved.length >= 8;
    });
  }

  void _addSlot() {
    if (_configs.length >= kMaxCameras) return;
    setState(() {
      final i = _configs.length;
      _configs = [
        ..._configs,
        CameraSlotConfig(
          port: kBasePort + i,
          cameraIndex: i,
          label: 'Cámara ${i + 1}',
          zone: kHospitalZones[i % kHospitalZones.length],
          priority: CameraPriority.normal,
          frameSkip: 5,
          snapshotQuality: 75,
        ),
      ];
      _showResourceWarning = _configs.length >= 8;
    });
  }

  void _removeSlot(int index) {
    if (_configs.length <= 1) return;
    setState(() {
      final updated = List<CameraSlotConfig>.from(_configs)..removeAt(index);
      _configs = List.generate(
        updated.length,
        (i) => updated[i].copyWith(port: kBasePort + i),
      );
      _showResourceWarning = _configs.length >= 8;
    });
  }

  void _applyHospitalPreset() async {
    final count = await showDialog<int>(
      context: context,
      builder: (ctx) => _PresetDialog(current: _configs.length),
    );
    if (count == null) return;
    setState(() {
      _configs = MultiBackendManager.hospitalPreset(count);
      _showResourceWarning = count >= 8;
    });
  }

  void _updateSlot(int i, CameraSlotConfig updated) {
    setState(() {
      _configs = List<CameraSlotConfig>.from(_configs)..[i] = updated;
    });
  }

  Future<void> _launch() async {
    await MultiBackendManager.saveConfig(_configs);
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => MultiCameraScreen(
          configs: _configs,
          companyId: widget.companyId,
          companyName: widget.companyName,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ram = MultiBackendManager.estimatedRamMb(_configs.length);
    final cpu = MultiBackendManager.estimatedCpuCores(_configs);

    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: const Text('Configuración de cámaras'),
        backgroundColor: const Color(0xFF1A237E),
        elevation: 0,
        actions: [
          TextButton.icon(
            onPressed: _applyHospitalPreset,
            icon: const Icon(Icons.local_hospital, color: Colors.cyanAccent, size: 18),
            label: const Text('Preset hospital',
                style: TextStyle(color: Colors.cyanAccent, fontSize: 13)),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: Colors.cyanAccent))
          : Column(
              children: [
                // ── Barra de recursos estimados ─────────────────────────
                _ResourceBar(
                  cameraCount: _configs.length,
                  ramMb: ram,
                  cpuCores: cpu,
                  showWarning: _showResourceWarning,
                ),

                // ── Lista de slots ──────────────────────────────────────
                Expanded(
                  child: ListView.separated(
                    padding: const EdgeInsets.all(16),
                    itemCount: _configs.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (ctx, i) => _SlotConfigCard(
                      config: _configs[i],
                      slotNumber: i + 1,
                      canRemove: _configs.length > 1,
                      onRemove: () => _removeSlot(i),
                      onChanged: (updated) => _updateSlot(i, updated),
                    ),
                  ),
                ),

                // ── Acciones ────────────────────────────────────────────
                Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  child: Column(
                    children: [
                      if (_configs.length < kMaxCameras)
                        OutlinedButton.icon(
                          onPressed: _addSlot,
                          icon: const Icon(Icons.add_circle_outline,
                              color: Colors.cyanAccent),
                          label: Text(
                            'Agregar cámara (${_configs.length}/$kMaxCameras)',
                            style: const TextStyle(color: Colors.cyanAccent),
                          ),
                          style: OutlinedButton.styleFrom(
                            side: const BorderSide(color: Colors.cyanAccent),
                            minimumSize: const Size.fromHeight(44),
                          ),
                        ),
                      const SizedBox(height: 10),
                      ElevatedButton.icon(
                        onPressed: _launch,
                        icon: const Icon(Icons.play_circle_fill),
                        label: Text(
                            'Iniciar ${_configs.length} cámara${_configs.length > 1 ? "s" : ""}'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF1A237E),
                          foregroundColor: Colors.white,
                          minimumSize: const Size.fromHeight(50),
                          textStyle: const TextStyle(
                              fontSize: 16, fontWeight: FontWeight.bold),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
    );
  }
}

// ─── Diálogo de preset ────────────────────────────────────────────────────────

class _PresetDialog extends StatefulWidget {
  final int current;
  const _PresetDialog({required this.current});

  @override
  State<_PresetDialog> createState() => _PresetDialogState();
}

class _PresetDialogState extends State<_PresetDialog> {
  late int _count;

  @override
  void initState() {
    super.initState();
    _count = widget.current.clamp(1, kMaxCameras);
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: const Color(0xFF1E1E2E),
      title: const Text('Preset hospitalario',
          style: TextStyle(color: Colors.white)),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            'Genera configuración óptima automáticamente con zonas y prioridades hospitalarias.',
            style: TextStyle(color: Colors.white70, fontSize: 13),
          ),
          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                onPressed: _count > 1
                    ? () => setState(() => _count--)
                    : null,
                icon: const Icon(Icons.remove_circle_outline,
                    color: Colors.cyanAccent),
              ),
              Text('$_count cámaras',
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 20,
                      fontWeight: FontWeight.bold)),
              IconButton(
                onPressed: _count < kMaxCameras
                    ? () => setState(() => _count++)
                    : null,
                icon: const Icon(Icons.add_circle_outline,
                    color: Colors.cyanAccent),
              ),
            ],
          ),
          Text(
            'RAM estimada: ~${MultiBackendManager.estimatedRamMb(_count)} MB',
            style: TextStyle(
              color: _count >= 10 ? Colors.orangeAccent : Colors.white38,
              fontSize: 12,
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancelar',
                style: TextStyle(color: Colors.white54))),
        ElevatedButton(
          onPressed: () => Navigator.pop(context, _count),
          style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF1A237E)),
          child: const Text('Aplicar'),
        ),
      ],
    );
  }
}

// ─── Card de configuración de slot ───────────────────────────────────────────

class _SlotConfigCard extends StatefulWidget {
  final CameraSlotConfig config;
  final int slotNumber;
  final bool canRemove;
  final VoidCallback onRemove;
  final ValueChanged<CameraSlotConfig> onChanged;

  const _SlotConfigCard({
    required this.config,
    required this.slotNumber,
    required this.canRemove,
    required this.onRemove,
    required this.onChanged,
  });

  @override
  State<_SlotConfigCard> createState() => _SlotConfigCardState();
}

class _SlotConfigCardState extends State<_SlotConfigCard> {
  late TextEditingController _labelCtrl;
  late TextEditingController _idxCtrl;
  bool _expanded = false;

  @override
  void initState() {
    super.initState();
    _labelCtrl = TextEditingController(text: widget.config.label);
    _idxCtrl = TextEditingController(text: '${widget.config.cameraIndex}');
  }

  @override
  void dispose() {
    _labelCtrl.dispose();
    _idxCtrl.dispose();
    super.dispose();
  }

  void _emit(CameraSlotConfig updated) => widget.onChanged(updated);

  Color get _priorityColor {
    switch (widget.config.priority) {
      case CameraPriority.high:
        return Colors.redAccent;
      case CameraPriority.normal:
        return Colors.cyanAccent;
      case CameraPriority.low:
        return Colors.white38;
    }
  }

  @override
  Widget build(BuildContext context) {
    final cfg = widget.config;
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E2E),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _priorityColor.withValues(alpha: 0.35)),
      ),
      child: Column(
        children: [
          // ── Cabecera ──────────────────────────────────────────────────
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: Row(
                children: [
                  // Badge número
                  Container(
                    width: 26,
                    height: 26,
                    decoration: BoxDecoration(
                      color: _priorityColor.withValues(alpha: 0.2),
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: Text('${widget.slotNumber}',
                        style: TextStyle(
                            color: _priorityColor,
                            fontSize: 12,
                            fontWeight: FontWeight.bold)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          cfg.label.isNotEmpty
                              ? cfg.label
                              : 'Cámara ${widget.slotNumber}',
                          style: const TextStyle(
                              color: Colors.white,
                              fontSize: 14,
                              fontWeight: FontWeight.w600),
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          cfg.zone.isNotEmpty ? cfg.zone : 'Sin zona',
                          style: const TextStyle(
                              color: Colors.white38, fontSize: 11),
                        ),
                      ],
                    ),
                  ),
                  // Chips de info
                  _Chip('P${cfg.priority.name[0].toUpperCase()}',
                      _priorityColor),
                  const SizedBox(width: 4),
                  _Chip('S${cfg.frameSkip}', Colors.white38),
                  const SizedBox(width: 4),
                  _Chip(':${cfg.port}', Colors.white24),
                  if (widget.canRemove)
                    IconButton(
                      onPressed: widget.onRemove,
                      icon: const Icon(Icons.delete_outline,
                          color: Colors.redAccent, size: 18),
                      padding: const EdgeInsets.only(left: 4),
                      constraints: const BoxConstraints(),
                    ),
                  Icon(
                    _expanded
                        ? Icons.expand_less
                        : Icons.expand_more,
                    color: Colors.white38,
                    size: 18,
                  ),
                ],
              ),
            ),
          ),

          // ── Cuerpo expandible ─────────────────────────────────────────
          if (_expanded) ...[
            const Divider(color: Colors.white10, height: 1),
            Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                children: [
                  // Etiqueta
                  _Field(
                    controller: _labelCtrl,
                    hint: 'Etiqueta (ej. Recepción Principal)',
                    onChanged: (v) => _emit(cfg.copyWith(label: v)),
                  ),
                  const SizedBox(height: 8),
                  // Zona
                  DropdownButtonFormField<String>(
                    initialValue: kHospitalZones.contains(cfg.zone)
                        ? cfg.zone
                        : kHospitalZones.last,
                    dropdownColor: const Color(0xFF1E1E2E),
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                    decoration: _inputDeco('Zona hospitalaria'),
                    items: kHospitalZones
                        .map((z) => DropdownMenuItem(value: z, child: Text(z)))
                        .toList(),
                    onChanged: (z) {
                      if (z == null) return;
                      final auto = CameraSlotConfig.autoForZone(
                        port: cfg.port,
                        cameraIndex: cfg.cameraIndex,
                        zone: z,
                        label: cfg.label,
                      );
                      _emit(auto);
                    },
                  ),
                  const SizedBox(height: 8),
                  // Prioridad
                  DropdownButtonFormField<CameraPriority>(
                    initialValue: cfg.priority,
                    dropdownColor: const Color(0xFF1E1E2E),
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                    decoration: _inputDeco('Prioridad'),
                    items: CameraPriority.values
                        .map((p) => DropdownMenuItem(
                            value: p, child: Text(p.label)))
                        .toList(),
                    onChanged: (p) {
                      if (p == null) return;
                      _emit(cfg.copyWith(
                        priority: p,
                        frameSkip: p.recommendedFrameSkip,
                        snapshotQuality: p.recommendedJpegQuality,
                      ));
                    },
                  ),
                  const SizedBox(height: 8),
                  // Índice de cámara
                  _Field(
                    controller: _idxCtrl,
                    hint: 'Índice de cámara USB (0, 1, 2…)',
                    keyboardType: TextInputType.number,
                    onChanged: (v) {
                      final n = int.tryParse(v);
                      if (n != null && n >= 0) _emit(cfg.copyWith(cameraIndex: n));
                    },
                  ),
                  const SizedBox(height: 10),
                  // Frame skip slider
                  _SliderRow(
                    label: 'Frame skip',
                    value: cfg.frameSkip.toDouble(),
                    min: 1,
                    max: 15,
                    divisions: 14,
                    format: (v) => '${v.toInt()} frames',
                    hint: 'Más alto = menos CPU. Recomendado ${cfg.priority.recommendedFrameSkip} para ${cfg.priority.label}.',
                    color: Colors.cyanAccent,
                    onChanged: (v) => _emit(cfg.copyWith(frameSkip: v.toInt())),
                  ),
                  const SizedBox(height: 6),
                  // JPEG quality slider
                  _SliderRow(
                    label: 'Calidad JPEG',
                    value: cfg.snapshotQuality.toDouble(),
                    min: 50,
                    max: 95,
                    divisions: 9,
                    format: (v) => '${v.toInt()}%',
                    hint: 'Menos calidad = más velocidad. Recomendado ${cfg.priority.recommendedJpegQuality}%.',
                    color: Colors.orangeAccent,
                    onChanged: (v) =>
                        _emit(cfg.copyWith(snapshotQuality: v.toInt())),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ─── Barra de recursos estimados ─────────────────────────────────────────────

class _ResourceBar extends StatelessWidget {
  final int cameraCount;
  final int ramMb;
  final double cpuCores;
  final bool showWarning;

  const _ResourceBar({
    required this.cameraCount,
    required this.ramMb,
    required this.cpuCores,
    required this.showWarning,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: showWarning
          ? Colors.orange.withValues(alpha: 0.08)
          : Colors.white.withValues(alpha: 0.03),
      child: Row(
        children: [
          Icon(
            showWarning ? Icons.warning_amber_rounded : Icons.memory,
            color: showWarning ? Colors.orangeAccent : Colors.white38,
            size: 16,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              showWarning
                  ? '$cameraCount cámaras — RAM estimada ~${ramMb >= 1024 ? "${(ramMb / 1024).toStringAsFixed(1)} GB" : "$ramMb MB"} · CPU ~${cpuCores.toStringAsFixed(1)} núcleos. Se recomienda GPU dedicada.'
                  : '$cameraCount cámaras — RAM ~$ramMb MB · CPU ~${cpuCores.toStringAsFixed(1)} núcleos',
              style: TextStyle(
                  color: showWarning ? Colors.orangeAccent : Colors.white38,
                  fontSize: 11),
            ),
          ),
        ],
      ),
    );
  }
}

// ─── Helpers de UI ────────────────────────────────────────────────────────────

class _Chip extends StatelessWidget {
  final String text;
  final Color color;
  const _Chip(this.text, this.color);

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(text,
            style: TextStyle(
                color: color, fontSize: 10, fontWeight: FontWeight.bold)),
      );
}

class _Field extends StatelessWidget {
  final TextEditingController controller;
  final String hint;
  final ValueChanged<String> onChanged;
  final TextInputType? keyboardType;
  const _Field(
      {required this.controller,
      required this.hint,
      required this.onChanged,
      this.keyboardType});

  @override
  Widget build(BuildContext context) => TextField(
        controller: controller,
        onChanged: onChanged,
        keyboardType: keyboardType,
        style: const TextStyle(color: Colors.white, fontSize: 13),
        decoration: _inputDeco(hint),
      );
}

class _SliderRow extends StatelessWidget {
  final String label;
  final double value;
  final double min, max;
  final int divisions;
  final String Function(double) format;
  final String hint;
  final Color color;
  final ValueChanged<double> onChanged;

  const _SliderRow({
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.format,
    required this.hint,
    required this.color,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Text(label,
                style:
                    const TextStyle(color: Colors.white60, fontSize: 12)),
            const Spacer(),
            Text(format(value),
                style: TextStyle(
                    color: color,
                    fontSize: 12,
                    fontWeight: FontWeight.bold)),
          ]),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              activeTrackColor: color,
              thumbColor: color,
              inactiveTrackColor: color.withValues(alpha: 0.2),
              overlayColor: color.withValues(alpha: 0.1),
              trackHeight: 3,
              thumbShape:
                  const RoundSliderThumbShape(enabledThumbRadius: 7),
            ),
            child: Slider(
              value: value.clamp(min, max),
              min: min,
              max: max,
              divisions: divisions,
              onChanged: onChanged,
            ),
          ),
          Text(hint,
              style: const TextStyle(color: Colors.white24, fontSize: 10)),
        ],
      );
}

InputDecoration _inputDeco(String hint) => InputDecoration(
      hintText: hint,
      hintStyle: const TextStyle(color: Colors.white38, fontSize: 12),
      filled: true,
      fillColor: const Color(0xFF0D1117),
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(8),
        borderSide: BorderSide.none,
      ),
    );

// ═══════════════════════════════════════════════════════════════════════════
// PANTALLA PRINCIPAL MULTI-CÁMARA
// ═══════════════════════════════════════════════════════════════════════════

class MultiCameraScreen extends StatefulWidget {
  final List<CameraSlotConfig> configs;
  final String companyId;
  final String companyName;

  const MultiCameraScreen({
    super.key,
    required this.configs,
    required this.companyId,
    required this.companyName,
  });

  @override
  State<MultiCameraScreen> createState() => _MultiCameraScreenState();
}

class _MultiCameraScreenState extends State<MultiCameraScreen>
    with WidgetsBindingObserver {
  final _multiMgr = MultiBackendManager();
  final List<_CamSlotState> _slots = [];

  bool _starting = true;
  StartupProgress _progress = const StartupProgress();
  String? _fatalError;

  // Modo foco: índice de la cámara expandida (-1 = ninguna)
  int _focusedIndex = -1;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _startAll();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    for (final s in _slots) {
      s.dispose();
    }
    _multiMgr.stopAll();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) {
      // Pausar todos los streams cuando la app va a segundo plano
      for (final s in _slots) {
        s.pause();
      }
    } else if (state == AppLifecycleState.resumed) {
      for (final s in _slots) {
        if (s.isReady) s.resume();
      }
    } else if (state == AppLifecycleState.detached) {
      BackendManager.killAll(ports: _multiMgr.activePorts);
    }
  }

  // ── Inicio ──────────────────────────────────────────────────────────────

  Future<void> _startAll() async {
    setState(() {
      _starting = true;
      _fatalError = null;
      _slots.clear();
      _progress = const StartupProgress();
    });

    final errors = await _multiMgr.startAll(
      widget.configs,
      widget.companyId,
      onProgress: (p) {
        if (mounted) setState(() => _progress = p);
      },
    );

    if (!mounted) return;
    setState(() => _progress = StartupProgress(
          total: widget.configs.length,
          launched: widget.configs.length,
          currentAction: 'Verificando conexión...',
        ));

    final ready = await _multiMgr.waitForAll(
      onProgress: (p) {
        if (mounted) setState(() => _progress = p);
      },
    );

    if (!mounted) return;

    if (!ready.any((r) => r)) {
      setState(() {
        _starting = false;
        _fatalError = errors.whereType<String>().firstOrNull ??
            'No se pudo iniciar ningún backend.\n'
                'Verifica que Python esté instalado y las cámaras conectadas.';
      });
      return;
    }

    // Crear slots con polling escalonado para no saturar el sistema
    final n = widget.configs.length;
    for (var i = 0; i < n; i++) {
      final cfg = widget.configs[i];
      final slot = _CamSlotState(
        config: cfg,
        manager: _multiMgr.managers[i],
        client: BackendClient.forPort(cfg.port),
        isReady: ready[i],
        error: ready[i] ? null : (errors[i] ?? 'Backend no respondió'),
      );
      slot.onStateChanged = () {
        if (mounted) setState(() {});
      };
      _slots.add(slot);
    }

    setState(() => _starting = false);

    // Escalonar inicio de polling: separa los timers para evitar
    // que todos los frames lleguen al mismo tiempo (thundering herd).
    final baseInterval = 400; // ms entre frames por cámara
    final stagger = n > 1 ? (baseInterval / n).round() : 0;

    for (var i = 0; i < _slots.length; i++) {
      if (!_slots[i].isReady) continue;
      final delay = i * stagger;
      Future.delayed(Duration(milliseconds: delay), () {
        if (mounted && _slots[i].isReady) {
          _slots[i].startPolling(focused: i == _focusedIndex);
        }
      });
    }
  }

  // ── Foco ────────────────────────────────────────────────────────────────

  void _setFocus(int index) {
    if (_focusedIndex == index) {
      setState(() => _focusedIndex = -1);
      for (var i = 0; i < _slots.length; i++) {
        if (_slots[i].isReady) {
          _slots[i].startPolling(focused: false);
        }
      }
    } else {
      setState(() => _focusedIndex = index);
      for (var i = 0; i < _slots.length; i++) {
        if (_slots[i].isReady) {
          _slots[i].startPolling(focused: i == index);
        }
      }
    }
  }

  // ── Restart de slot individual ──────────────────────────────────────────

  Future<void> _restartSlot(int index) async {
    final slot = _slots[index];
    slot.stopPolling();
    slot.restarting = true;
    slot.error = null;
    if (mounted) setState(() {});

    await slot.manager.kill();

    final cfg = widget.configs[index];
    final newMgr = BackendManager(
      port: cfg.port,
      cameraIndex: cfg.cameraIndex,
      frameSkip: cfg.frameSkip,
      snapshotQuality: cfg.snapshotQuality,
    );
    final err = await newMgr.start(companyId: widget.companyId);

    if (err != null) {
      if (mounted) {
        setState(() {
          slot
            ..error = err
            ..restarting = false;
        });
      }
      return;
    }

    bool ok = false;
    for (var attempt = 0; attempt < 40; attempt++) {
      if (newMgr.didCrash) break;
      try {
        final r = await http
            .get(Uri.parse('http://127.0.0.1:${cfg.port}/api/health'))
            .timeout(const Duration(seconds: 2));
        if (r.statusCode == 200) {
          final dir = newMgr.backendDir;
          if (dir != null) {
            await BackendClient.forPort(cfg.port)
                .loadToken(dir, instanceId: newMgr.instanceId);
          }
          ok = true;
          break;
        }
      } catch (_) {}
      await Future.delayed(const Duration(milliseconds: 500));
    }

    if (!mounted) return;
    setState(() {
      slot
        ..manager = newMgr
        ..isReady = ok
        ..restarting = false
        ..autoRestartAttempts = 0
        ..error = ok ? null : 'Backend no respondió al reiniciar.';
    });
    if (ok) slot.startPolling(focused: index == _focusedIndex);
  }

  // ── Build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: _buildAppBar(),
      body: _buildBody(),
    );
  }

  AppBar _buildAppBar() {
    final online = _slots.where((s) => s.isReady && !s.restarting).length;
    final total = _slots.length;

    return AppBar(
      title: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'BioFace — ${widget.companyName}',
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
          ),
          if (!_starting)
            Text(
              '$online/$total cámaras activas',
              style: TextStyle(
                fontSize: 11,
                color: online == total
                    ? Colors.greenAccent
                    : online == 0
                        ? Colors.redAccent
                        : Colors.orangeAccent,
              ),
            ),
        ],
      ),
      backgroundColor: const Color(0xFF1A237E),
      elevation: 0,
      actions: [
        if (!_starting && _focusedIndex >= 0)
          IconButton(
            icon: const Icon(Icons.fullscreen_exit),
            tooltip: 'Salir del modo foco',
            onPressed: () => _setFocus(_focusedIndex),
          ),
        IconButton(
          icon: const Icon(Icons.settings),
          tooltip: 'Reconfigurar',
          onPressed: _starting
              ? null
              : () async {
                  final nav = Navigator.of(context);
                  await _multiMgr.stopAll();
                  if (!mounted) return;
                  nav.pushReplacement(
                    MaterialPageRoute(
                      builder: (_) => MultiCameraSetupScreen(
                        companyId: widget.companyId,
                        companyName: widget.companyName,
                      ),
                    ),
                  );
                },
        ),
      ],
    );
  }

  Widget _buildBody() {
    if (_starting) return _buildStartupProgress();
    if (_fatalError != null) return _buildFatalError();

    // Modo foco: una cámara ocupa toda la pantalla
    if (_focusedIndex >= 0 && _focusedIndex < _slots.length) {
      return GestureDetector(
        onDoubleTap: () => _setFocus(_focusedIndex),
        child: _CamSlotWidget(
          slot: _slots[_focusedIndex],
          onRestart: () => _restartSlot(_focusedIndex),
          onTap: () => _setFocus(_focusedIndex),
          focused: true,
        ),
      );
    }

    return _buildGrid();
  }

  Widget _buildStartupProgress() {
    final pct = (_progress.fraction * 100).toStringAsFixed(0);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.videocam, color: Colors.cyanAccent, size: 48),
            const SizedBox(height: 24),
            const Text('Iniciando sistema multi-cámara...',
                style: TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Text(
              widget.configs.length > 1
                  ? '${widget.configs.length} cámaras — puede tardar hasta ${(widget.configs.length * 3).round()} segundos'
                  : '1 cámara',
              style: const TextStyle(color: Colors.white54, fontSize: 13),
            ),
            const SizedBox(height: 24),
            LinearProgressIndicator(
              value: _progress.fraction > 0 ? _progress.fraction : null,
              color: Colors.cyanAccent,
              backgroundColor: Colors.white12,
              minHeight: 6,
              borderRadius: BorderRadius.circular(3),
            ),
            const SizedBox(height: 12),
            Text(
              _progress.currentAction.isNotEmpty
                  ? _progress.currentAction
                  : 'Preparando...',
              style: const TextStyle(color: Colors.white38, fontSize: 12),
            ),
            if (_progress.total > 0) ...[
              const SizedBox(height: 6),
              Text(
                '$pct% — ${_progress.ready} listas · ${_progress.failed} fallidas',
                style: const TextStyle(color: Colors.white24, fontSize: 11),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildFatalError() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, color: Colors.redAccent, size: 64),
            const SizedBox(height: 16),
            Text(_fatalError!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70, fontSize: 15)),
            const SizedBox(height: 24),
            ElevatedButton.icon(
              onPressed: _startAll,
              icon: const Icon(Icons.refresh),
              label: const Text('Reintentar'),
              style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF1A237E)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildGrid() {
    final n = _slots.length;

    // Columnas responsivas: 1 → 1col, 2-4 → 2col, 5+ → 3col
    final cols = n == 1
        ? 1
        : n <= 4
            ? 2
            : 3;

    // Agrupar por zona si hay suficientes cámaras
    final grouped = _groupByZone(_slots);
    final showGroups = n >= 5 && grouped.length > 1;

    if (showGroups) {
      return _buildGroupedGrid(grouped, cols);
    }

    return _buildFlatGrid(cols);
  }

  Widget _buildFlatGrid(int cols) {
    return GridView.builder(
      padding: const EdgeInsets.all(8),
      gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: cols,
        crossAxisSpacing: 6,
        mainAxisSpacing: 6,
        childAspectRatio: 16 / 10,
      ),
      itemCount: _slots.length,
      itemBuilder: (ctx, i) => RepaintBoundary(
        child: _CamSlotWidget(
          slot: _slots[i],
          onRestart: () => _restartSlot(i),
          onTap: () => _setFocus(i),
          focused: false,
        ),
      ),
    );
  }

  Widget _buildGroupedGrid(
      Map<String, List<int>> grouped, int cols) {
    return CustomScrollView(
      slivers: [
        for (final entry in grouped.entries) ...[
          // Cabecera de zona
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 14, 12, 6),
              child: Row(
                children: [
                  const Icon(Icons.location_on,
                      color: Colors.cyanAccent, size: 14),
                  const SizedBox(width: 6),
                  Text(
                    entry.key,
                    style: const TextStyle(
                        color: Colors.cyanAccent,
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 0.5),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '${entry.value.length} cámara${entry.value.length > 1 ? "s" : ""}',
                    style: const TextStyle(
                        color: Colors.white38, fontSize: 11),
                  ),
                ],
              ),
            ),
          ),
          // Grid de la zona
          SliverPadding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            sliver: SliverGrid(
              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: cols,
                crossAxisSpacing: 6,
                mainAxisSpacing: 6,
                childAspectRatio: 16 / 10,
              ),
              delegate: SliverChildBuilderDelegate(
                (ctx, j) {
                  final i = entry.value[j];
                  return RepaintBoundary(
                    child: _CamSlotWidget(
                      slot: _slots[i],
                      onRestart: () => _restartSlot(i),
                      onTap: () => _setFocus(i),
                      focused: false,
                    ),
                  );
                },
                childCount: entry.value.length,
              ),
            ),
          ),
        ],
      ],
    );
  }

  Map<String, List<int>> _groupByZone(List<_CamSlotState> slots) {
    final result = <String, List<int>>{};
    for (var i = 0; i < slots.length; i++) {
      final zone = slots[i].config.zone.isNotEmpty
          ? slots[i].config.zone
          : 'Sin zona';
      (result[zone] ??= []).add(i);
    }
    return result;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ESTADO INTERNO DE UN SLOT
// ═══════════════════════════════════════════════════════════════════════════

class _CamSlotState {
  final CameraSlotConfig config;
  BackendManager manager;
  final BackendClient client;

  bool isReady;
  bool restarting = false;
  String? error;
  int autoRestartAttempts = 0;
  static const int maxAutoRestarts = 3;

  Uint8List? frame;
  Map<String, dynamic>? lastDetection;
  String? lastPersonName;
  String status = 'starting';
  int totalDetections = 0;

  Timer? _pollTimer;
  Timer? _frameTimer;
  Timer? _autoRestartTimer;

  bool _paused = false;
  int _heartbeatFails = 0;

  VoidCallback? onStateChanged;
  VoidCallback? onAutoRestartNeeded;

  _CamSlotState({
    required this.config,
    required this.manager,
    required this.client,
    this.isReady = false,
    this.error,
  });

  void startPolling({bool focused = false}) {
    _paused = false;
    _pollTimer?.cancel();
    _frameTimer?.cancel();

    final frameInterval = config.priority.frameIntervalMs(focused: focused);

    _pollTimer = Timer.periodic(
        const Duration(seconds: 1), (_) => _fetchStatus());
    _frameTimer = Timer.periodic(
        Duration(milliseconds: frameInterval), (_) => _fetchFrame());
  }

  void stopPolling() {
    _pollTimer?.cancel();
    _frameTimer?.cancel();
    _autoRestartTimer?.cancel();
  }

  void pause() {
    _paused = true;
    _frameTimer?.cancel();
    // Mantener _pollTimer activo para detectar crashes
  }

  void resume() {
    if (!_paused) return;
    _paused = false;
    startPolling();
  }

  void dispose() {
    stopPolling();
    onStateChanged = null;
    onAutoRestartNeeded = null;
  }

  Future<void> _fetchFrame() async {
    if (_paused) return;
    try {
      final r = await http
          .get(Uri.parse('http://127.0.0.1:${config.port}/api/snapshot'))
          .timeout(const Duration(seconds: 1));
      if (r.statusCode == 200 && r.bodyBytes.isNotEmpty) {
        frame = r.bodyBytes;
        onStateChanged?.call();
      }
    } catch (_) {}
  }

  Future<void> _fetchStatus() async {
    try {
      final r = await client
          .get('/api/status')
          .timeout(const Duration(seconds: 3));
      if (r.statusCode == 200) {
        _heartbeatFails = 0;
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        status = data['status'] as String? ?? 'unknown';
        lastPersonName = data['last_person_name'] as String?;
        lastDetection = data['last_detection'] as Map<String, dynamic>?;
        totalDetections = data['total_detections'] as int? ?? totalDetections;
        onStateChanged?.call();
      }
    } catch (_) {
      _heartbeatFails++;
      if (_heartbeatFails >= 3 && manager.didCrash) {
        _scheduleAutoRestart();
      }
    }
  }

  void _scheduleAutoRestart() {
    if (autoRestartAttempts >= maxAutoRestarts) {
      stopPolling();
      isReady = false;
      error = 'Backend caído ($maxAutoRestarts intentos fallidos).';
      onStateChanged?.call();
      return;
    }
    autoRestartAttempts++;
    final delaySec = pow(2, autoRestartAttempts).toInt(); // 2, 4, 8s
    stopPolling();
    status = 'restarting';
    onStateChanged?.call();
    _autoRestartTimer = Timer(
        Duration(seconds: delaySec), () => onAutoRestartNeeded?.call());
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// WIDGET VISUAL DE UN SLOT DE CÁMARA
// ═══════════════════════════════════════════════════════════════════════════

class _CamSlotWidget extends StatefulWidget {
  final _CamSlotState slot;
  final VoidCallback onRestart;
  final VoidCallback onTap;
  final bool focused;

  const _CamSlotWidget({
    required this.slot,
    required this.onRestart,
    required this.onTap,
    required this.focused,
  });

  @override
  State<_CamSlotWidget> createState() => _CamSlotWidgetState();
}

class _CamSlotWidgetState extends State<_CamSlotWidget> {
  @override
  void initState() {
    super.initState();
    widget.slot.onStateChanged = () {
      if (mounted) setState(() {});
    };
    widget.slot.onAutoRestartNeeded = widget.onRestart;
  }

  @override
  void didUpdateWidget(_CamSlotWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.slot != widget.slot) {
      oldWidget.slot.onStateChanged = null;
      widget.slot.onStateChanged = () {
        if (mounted) setState(() {});
      };
      widget.slot.onAutoRestartNeeded = widget.onRestart;
    }
  }

  @override
  void dispose() {
    widget.slot.onStateChanged = null;
    super.dispose();
  }

  Color get _priorityAccent {
    switch (widget.slot.config.priority) {
      case CameraPriority.high:
        return Colors.redAccent;
      case CameraPriority.normal:
        return Colors.cyanAccent;
      case CameraPriority.low:
        return Colors.white38;
    }
  }

  @override
  Widget build(BuildContext context) {
    final slot = widget.slot;

    return GestureDetector(
      onTap: widget.onTap,
      child: Container(
        decoration: BoxDecoration(
          color: const Color(0xFF0D1117),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(
            color: widget.focused
                ? Colors.cyanAccent.withValues(alpha: 0.6)
                : _priorityAccent.withValues(alpha: 0.2),
            width: widget.focused ? 2 : 1,
          ),
        ),
        clipBehavior: Clip.antiAlias,
        child: slot.restarting
            ? _buildRestarting()
            : !slot.isReady
                ? _buildError(slot.error)
                : _buildLive(slot),
      ),
    );
  }

  Widget _buildRestarting() => const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                  strokeWidth: 2, color: Colors.cyanAccent),
            ),
            SizedBox(height: 8),
            Text('Reiniciando...',
                style: TextStyle(color: Colors.white54, fontSize: 11)),
          ],
        ),
      );

  Widget _buildError(String? msg) => Center(
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.videocam_off,
                  color: Colors.redAccent.withValues(alpha: 0.7), size: 28),
              const SizedBox(height: 6),
              Text(
                msg ?? 'No disponible',
                textAlign: TextAlign.center,
                style:
                    const TextStyle(color: Colors.white38, fontSize: 10),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 8),
              GestureDetector(
                onTap: widget.onRestart,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.redAccent.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(
                        color: Colors.redAccent.withValues(alpha: 0.4)),
                  ),
                  child: const Text('Reiniciar',
                      style:
                          TextStyle(color: Colors.redAccent, fontSize: 10)),
                ),
              ),
            ],
          ),
        ),
      );

  Widget _buildLive(_CamSlotState slot) {
    final hasFrame = slot.frame != null;
    final isLive = slot.status == 'running';

    return Stack(
      fit: StackFit.expand,
      children: [
        // Frame
        if (hasFrame)
          Image.memory(
            slot.frame!,
            fit: BoxFit.cover,
            gaplessPlayback: true,
            errorBuilder: (_, _, _) => _placeholder(isLive),
          )
        else
          _placeholder(isLive),

        // ── Top-left: indicador LIVE + prioridad ─────────────────────
        Positioned(
          top: 5,
          left: 5,
          child: Row(
            children: [
              if (hasFrame)
                _LiveBadge(color: isLive ? Colors.greenAccent : Colors.orangeAccent),
              const SizedBox(width: 4),
              if (slot.config.priority == CameraPriority.high)
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 4, vertical: 1),
                  decoration: BoxDecoration(
                    color: Colors.redAccent.withValues(alpha: 0.7),
                    borderRadius: BorderRadius.circular(3),
                  ),
                  child: const Text('CRÍTICA',
                      style: TextStyle(
                          color: Colors.white,
                          fontSize: 8,
                          fontWeight: FontWeight.bold)),
                ),
            ],
          ),
        ),

        // ── Top-right: etiqueta de cámara ────────────────────────────
        Positioned(
          top: 5,
          right: 5,
          child: Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
            decoration: BoxDecoration(
              color: Colors.black54,
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              slot.config.label.isNotEmpty
                  ? slot.config.label
                  : 'Cámara ${slot.config.cameraIndex}',
              style: const TextStyle(
                  color: Colors.white, fontSize: 9, fontWeight: FontWeight.w600),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ),

        // ── Bottom: persona detectada ─────────────────────────────────
        if (slot.lastPersonName != null)
          Positioned(
            bottom: 0,
            left: 0,
            right: 0,
            child: Container(
              padding: const EdgeInsets.symmetric(
                  horizontal: 8, vertical: 5),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    Colors.transparent,
                    Colors.black.withValues(alpha: 0.85),
                  ],
                ),
              ),
              child: Row(
                children: [
                  const Icon(Icons.person,
                      color: Colors.cyanAccent, size: 12),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      slot.lastPersonName!,
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 11,
                          fontWeight: FontWeight.bold),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (slot.lastDetection != null)
                    Text(
                      '${(((slot.lastDetection!['similarity'] as num?) ?? 0) * 100).toStringAsFixed(0)}%',
                      style: const TextStyle(
                          color: Colors.greenAccent,
                          fontSize: 10,
                          fontWeight: FontWeight.bold),
                    ),
                ],
              ),
            ),
          ),

        // ── Icono de foco ─────────────────────────────────────────────
        if (!widget.focused)
          Positioned(
            bottom: 5,
            right: 5,
            child: Icon(Icons.fullscreen,
                color: Colors.white24, size: 14),
          ),
      ],
    );
  }

  Widget _placeholder(bool isLive) => Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            isLive ? Icons.videocam : Icons.videocam_off,
            color: Colors.white12,
            size: 28,
          ),
          const SizedBox(height: 4),
          Text(
            isLive ? 'Conectando...' : 'Sin señal',
            style: const TextStyle(color: Colors.white12, fontSize: 10),
          ),
        ],
      );
}

class _LiveBadge extends StatelessWidget {
  final Color color;
  const _LiveBadge({required this.color});

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
        decoration: BoxDecoration(
          color: Colors.black54,
          borderRadius: BorderRadius.circular(3),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
                width: 4,
                height: 4,
                decoration:
                    BoxDecoration(color: color, shape: BoxShape.circle)),
            const SizedBox(width: 3),
            Text('EN VIVO',
                style: TextStyle(
                    color: color,
                    fontSize: 8,
                    fontWeight: FontWeight.bold)),
          ],
        ),
      );
}
