import 'dart:async';

import 'package:flutter/material.dart';

import '../services/source_service.dart';

// ────────────────────────────────────────────────────────────────────────────
// Controller para controlar SourceSelector desde fuera
// ────────────────────────────────────────────────────────────────────────────

/// Permite al padre refrescar la lista o iniciar escaneo DroidCam.
class SourceSelectorController {
  final _refreshSources = <void Function()>{};
  final _scanDroidcam = <void Function()>{};

  /// Registra una función de refresco (llamado internamente).
  void _registerRefresh(void Function() fn) => _refreshSources.add(fn);

  /// Cancela registro de función de refresco.
  void _unregisterRefresh(void Function() fn) => _refreshSources.remove(fn);

  /// Registra una función de escaneo (llamado internamente).
  void _registerScanDroidcam(void Function() fn) => _scanDroidcam.add(fn);

  /// Cancela registro de función de escaneo.
  void _unregisterScanDroidcam(void Function() fn) => _scanDroidcam.remove(fn);

  /// Solicita al widget que recargue la lista de fuentes.
  void refresh() {
    for (final fn in _refreshSources) {
      fn();
    }
  }

  /// Solicita al widget que inicie escaneo DroidCam.
  void scanDroidcam() {
    for (final fn in _scanDroidcam) {
      fn();
    }
  }
}

// ────────────────────────────────────────────────────────────────────────────
// SourceSelector
// ────────────────────────────────────────────────────────────────────────────

/// Selector reutilizable de fuentes de video (USB + DroidCam).
///
/// Integra SourceService internamente, maneja su propio estado de carga,
/// y notifica al padre via callbacks.
///
/// Uso mínimo:
/// ```dart
/// SourceSelector(
///   sourceService: myService,
///   onSourceChanged: (source) => print('Fuente: ${source.name}'),
/// )
/// ```
class SourceSelector extends StatefulWidget {
  final SourceService sourceService;
  final SourceInfo? initialSource;
  final ValueChanged<SourceInfo>? onSourceChanged;
  final ValueChanged<SourceSelectResult?>? onDroidcamResult;
  final SourceSelectorController? controller;

  const SourceSelector({
    super.key,
    required this.sourceService,
    this.initialSource,
    this.onSourceChanged,
    this.onDroidcamResult,
    this.controller,
  });

  @override
  State<SourceSelector> createState() => _SourceSelectorState();
}

class _SourceSelectorState extends State<SourceSelector> {
  late SourceService _service;
  List<SourceInfo> _sources = [];
  SourceInfo? _selected;

  bool _loading = true;
  bool _scanningDroidcam = false;

  // Referencias estables para register/unregister (evita issues de tear-off identity)
  late final VoidCallback _refreshHandler;
  late final VoidCallback _scanHandler;

  @override
  void initState() {
    super.initState();
    _service = widget.sourceService;
    _selected = widget.initialSource;
    _refreshHandler = _loadSources;
    _scanHandler = _startDroidcamScan;
    widget.controller?._registerRefresh(_refreshHandler);
    widget.controller?._registerScanDroidcam(_scanHandler);
    _loadSources();
  }

  @override
  void dispose() {
    widget.controller?._unregisterRefresh(_refreshHandler);
    widget.controller?._unregisterScanDroidcam(_scanHandler);
    super.dispose();
  }

  // ── Carga de fuentes ───────────────────────────────────────────────

  Future<void> _loadSources() async {
    if (!mounted) return;
    setState(() => _loading = true);
    try {
      final sources = await _service.listSources();
      if (!mounted) return;
      setState(() {
        _sources = sources;
        _loading = false;
        // Si la fuente seleccionada ya no existe, resetear
        if (_selected != null &&
            !sources.any((s) => s.index == _selected!.index)) {
          _selected = sources.isNotEmpty ? sources.first : null;
          if (_selected != null) {
            widget.onSourceChanged?.call(_selected!);
          }
        }
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _sources = [];
        _loading = false;
      });
    }
  }

  // ── Selección ──────────────────────────────────────────────────────

  Future<void> _selectSource(SourceInfo source) async {
    final result = await _service.selectSourceInfo(source);
    if (!mounted) return;
    if (result.ok) {
      setState(() => _selected = source);
      widget.onSourceChanged?.call(source);
    } else {
      debugPrint('[SourceSelector] Error seleccionando ${source.name}: ${result.error}');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${result.error ?? "No se pudo abrir la fuente"}'),
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    }
  }

  // ── Escaneo DroidCam ───────────────────────────────────────────────

  Future<void> _startDroidcamScan() async {
    if (_scanningDroidcam) return;
    setState(() => _scanningDroidcam = true);
    try {
      final result = await _service.scanAndSelectDroidcam();
      if (!mounted) return;
      if (result != null && result.ok) {
        await _loadSources();
        setState(() {
          _selected = SourceInfo(
            index: -1,
            name: 'DroidCam',
            source: 'droidcam',
          );
        });
        widget.onSourceChanged?.call(_selected!);
      }
      widget.onDroidcamResult?.call(result);
    } finally {
      if (mounted) setState(() => _scanningDroidcam = false);
    }
  }

  // ── Build ──────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return _buildLoadingTile();
    }

    if (_sources.isEmpty) {
      return _buildEmptyTile();
    }

    return _buildSelector();
  }

  Widget _buildLoadingTile() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E2E),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white12),
      ),
      child: Row(
        children: [
          const SizedBox(
            width: 16, height: 16,
            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.cyanAccent),
          ),
          const SizedBox(width: 12),
          const Text('Escaneando fuentes...',
              style: TextStyle(color: Colors.white54, fontSize: 14)),
        ],
      ),
    );
  }

  Widget _buildEmptyTile() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E2E),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white12),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                const Row(
                  children: [
                    Icon(Icons.videocam_off, size: 16, color: Colors.white38),
                    SizedBox(width: 8),
                    Text('No se encontraron fuentes',
                        style: TextStyle(color: Colors.white38, fontSize: 14)),
                  ],
                ),
                const SizedBox(height: 4),
                const Text(
                  'Conecta la cámara o activa DroidCam y presiona Escanear',
                  style: TextStyle(color: Colors.white24, fontSize: 11),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          _buildRefreshButton(),
        ],
      ),
    );
  }

  Widget _buildSelector() {
    final selectedName = _selected?.name ?? _sources.first.name;
    final isDroidcamSelected = _selected?.isDroidcam ?? false;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E2E),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white12),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Expanded(
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<int>(
                    value: _selected != null &&
                            _sources.any((s) => s.index == _selected!.index)
                        ? _selected!.index
                        : (_sources.isNotEmpty ? _sources.first.index : null),
                    isExpanded: true,
                    dropdownColor: const Color(0xFF1E1E2E),
                    style: const TextStyle(color: Colors.white, fontSize: 14),
                    hint: Row(
                      children: [
                        Icon(
                          isDroidcamSelected ? Icons.wifi : Icons.videocam,
                          size: 18,
                          color: Colors.cyanAccent,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            selectedName,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white, fontSize: 14),
                          ),
                        ),
                      ],
                    ),
                    icon: const Icon(Icons.keyboard_arrow_down, color: Colors.white54),
                    items: _sources.map((source) {
                      final sd = source.isDroidcam;
                      return DropdownMenuItem(
                        value: source.index,
                        child: Row(
                          children: [
                            Icon(
                              sd ? Icons.wifi : Icons.videocam,
                              size: 18,
                              color: sd ? Colors.lightBlueAccent : Colors.cyanAccent,
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Text(
                                    source.name,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                        color: Colors.white, fontSize: 14),
                                  ),
                                  if (source.backend.isNotEmpty)
                                    Text(
                                      source.backend,
                                      style: const TextStyle(
                                          color: Colors.white38, fontSize: 11),
                                    ),
                                ],
                              ),
                            ),
                            const SizedBox(width: 8),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: sd
                                    ? Colors.lightBlueAccent.withValues(alpha: 0.15)
                                    : Colors.cyanAccent.withValues(alpha: 0.15),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                sd ? 'Wi-Fi' : 'USB',
                                style: TextStyle(
                                  color: sd
                                      ? Colors.lightBlueAccent.withValues(alpha: 0.8)
                                      : Colors.cyanAccent.withValues(alpha: 0.8),
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                          ],
                        ),
                      );
                    }).toList(),
                    onChanged: (value) {
                      if (value == null) return;
                      final source = _sources.firstWhere(
                        (s) => s.index == value,
                        orElse: () =>
                            SourceInfo(index: value, name: 'Cámara $value'),
                      );
                      _selectSource(source);
                    },
                  ),
                ),
              ),
              const SizedBox(width: 4),
              _buildRefreshButton(),
            ],
          ),
          const SizedBox(height: 6),
          // Botón DroidCam
          SizedBox(
            width: double.infinity,
            child: ElevatedButton.icon(
              onPressed: _scanningDroidcam ? null : _startDroidcamScan,
              icon: _scanningDroidcam
                  ? const SizedBox(
                      width: 14, height: 14,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white),
                    )
                  : const Icon(Icons.wifi_find, size: 16),
              label: Text(
                _scanningDroidcam
                    ? 'Buscando DroidCam...'
                    : 'Buscar DroidCam en la red',
                style: const TextStyle(fontSize: 12),
              ),
              style: ElevatedButton.styleFrom(
                backgroundColor:
                    Colors.lightBlueAccent.withValues(alpha: 0.15),
                foregroundColor: Colors.lightBlueAccent,
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                minimumSize: Size.zero,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildRefreshButton() {
    return IconButton(
      onPressed: _loading ? null : _loadSources,
      icon: _loading
          ? const SizedBox(
              width: 16, height: 16,
              child: CircularProgressIndicator(
                  strokeWidth: 2, color: Colors.white38),
            )
          : const Icon(Icons.refresh, size: 20),
      tooltip: 'Re-escanear fuentes',
      color: Colors.white54,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
    );
  }
}
