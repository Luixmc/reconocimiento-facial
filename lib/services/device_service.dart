import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

/// Gestiona el ID único permanente del dispositivo (terminal biométrica).
/// Se genera en el primer arranque y persiste indefinidamente.
class DeviceService {
  DeviceService._();

  static const _keyDeviceUid = 'bioface_device_uid';
  static const _keyKioskPin  = 'bioface_kiosk_pin';

  static String? _cachedUid;

  /// Retorna el UID permanente del dispositivo.
  /// Formato: BF-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX (UUID v4 sin guiones, prefijado)
  static Future<String> getOrCreateDeviceUid() async {
    if (_cachedUid != null) return _cachedUid!;

    final prefs = await SharedPreferences.getInstance();
    final saved  = prefs.getString(_keyDeviceUid);
    if (saved != null && saved.isNotEmpty) {
      _cachedUid = saved;
      return saved;
    }

    // Generar UUID v4 permanente
    final uuid = const Uuid().v4().replaceAll('-', '').toUpperCase();
    final uid  = 'BF-$uuid';
    await prefs.setString(_keyDeviceUid, uid);
    _cachedUid = uid;
    return uid;
  }

  /// Elimina el UID guardado (solo para tests/desarrollo).
  static Future<void> clearDeviceUid() async {
    _cachedUid = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyDeviceUid);
  }

  // ── Kiosk PIN ─────────────────────────────────────────────────────────────

  static Future<String> getKioskPin() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_keyKioskPin) ?? '1234';
  }

  static Future<void> setKioskPin(String pin) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyKioskPin, pin);
  }

  static Future<bool> validatePin(String entered) async {
    final pin = await getKioskPin();
    return entered == pin;
  }

  // ── Info del dispositivo ─────────────────────────────────────────────────

  static String get hostname {
    try {
      return Platform.localHostname;
    } catch (_) {
      return 'unknown';
    }
  }

  static String get platform => Platform.operatingSystem;
}
