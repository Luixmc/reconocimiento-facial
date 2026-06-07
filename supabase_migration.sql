-- ═══════════════════════════════════════════════════════════════════════════
-- BioFace — Migración SaaS v2
-- Ejecutar en Supabase SQL Editor como superadmin/service_role
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Company access ID (código de login que les damos a los clientes) ───────
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS company_access_id TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS license_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_companies_access_id
  ON companies (company_access_id)
  WHERE company_access_id IS NOT NULL;

-- ── 2. Devices — campos extendidos ────────────────────────────────────────────
ALTER TABLE devices
  ADD COLUMN IF NOT EXISTS device_uid    TEXT UNIQUE,   -- ID permanente del PC
  ADD COLUMN IF NOT EXISTS app_version   TEXT,
  ADD COLUMN IF NOT EXISTS detections_today INT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS camera_configs   JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS last_ip       TEXT;

CREATE INDEX IF NOT EXISTS idx_devices_uid
  ON devices (device_uid)
  WHERE device_uid IS NOT NULL;

-- ── 3. System settings — configuración remota por empresa ─────────────────────
CREATE TABLE IF NOT EXISTS system_settings (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (company_id, key)
);

CREATE INDEX IF NOT EXISTS idx_system_settings_company
  ON system_settings (company_id);

-- Valores por defecto para nuevas empresas (ejecutar por empresa si se necesita)
-- INSERT INTO system_settings (company_id, key, value) VALUES
--   ('<company_id>', 'confidence_threshold', '0.7'),
--   ('<company_id>', 'capture_cooldown_seconds', '3.0'),
--   ('<company_id>', 'frame_skip', '5'),
--   ('<company_id>', 'kiosk_pin', '1234'),
--   ('<company_id>', 'snapshot_quality', '75');

-- ── 4. Función para validar login con company_access_id ───────────────────────
CREATE OR REPLACE FUNCTION validate_company_login(p_access_id TEXT)
RETURNS TABLE (
  company_id   UUID,
  company_name TEXT,
  is_active    BOOLEAN,
  license_ok   BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  RETURN QUERY
  SELECT
    c.id,
    c.name,
    c.is_active,
    COALESCE(
      c.is_active AND (c.license_expires_at IS NULL OR c.license_expires_at > now()),
      false
    ) AS license_ok
  FROM companies c
  WHERE c.company_access_id = p_access_id
  LIMIT 1;
END;
$$;

-- ── 5. Upsert device (registrar o actualizar terminal biométrica) ──────────────
CREATE OR REPLACE FUNCTION upsert_device(
  p_device_uid   TEXT,
  p_company_id   UUID,
  p_name         TEXT,
  p_app_version  TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_id UUID;
BEGIN
  INSERT INTO devices (device_uid, company_id, name, app_version, is_online, last_seen_at)
  VALUES (p_device_uid, p_company_id, p_name, p_app_version, true, now())
  ON CONFLICT (device_uid) DO UPDATE SET
    is_online    = true,
    last_seen_at = now(),
    app_version  = COALESCE(EXCLUDED.app_version, devices.app_version),
    company_id   = EXCLUDED.company_id
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

-- ── 6. Heartbeat device ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION heartbeat_device(
  p_device_uid       TEXT,
  p_detections_today INT DEFAULT 0,
  p_app_version      TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE devices SET
    is_online         = true,
    last_seen_at      = now(),
    detections_today  = p_detections_today,
    app_version       = COALESCE(p_app_version, app_version)
  WHERE device_uid = p_device_uid;
END;
$$;

-- ── 7. Get device config (camera_configs + system_settings) ───────────────────
CREATE OR REPLACE FUNCTION get_device_config(
  p_device_uid TEXT,
  p_company_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_camera_configs JSONB;
  v_settings       JSONB;
BEGIN
  SELECT COALESCE(camera_configs, '[]'::jsonb)
    INTO v_camera_configs
    FROM devices
   WHERE device_uid = p_device_uid;

  SELECT COALESCE(
    jsonb_object_agg(key, value),
    '{}'::jsonb
  )
    INTO v_settings
    FROM system_settings
   WHERE company_id = p_company_id;

  RETURN jsonb_build_object(
    'camera_configs',   COALESCE(v_camera_configs, '[]'::jsonb),
    'system_settings',  COALESCE(v_settings, '{}'::jsonb)
  );
END;
$$;

-- ── 8. Offline cuando no reporta heartbeat (job periódico) ────────────────────
-- Puedes programar esto en Supabase Edge Functions como cron job:
-- UPDATE devices SET is_online = false
-- WHERE last_seen_at < now() - INTERVAL '5 minutes';
--
-- O crear una función para llamar manualmente:
CREATE OR REPLACE FUNCTION mark_stale_devices_offline(
  p_threshold_minutes INT DEFAULT 5
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_count INT;
BEGIN
  UPDATE devices
  SET is_online = false
  WHERE is_online = true
    AND last_seen_at < now() - (p_threshold_minutes || ' minutes')::INTERVAL;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

-- ── 9. RLS para system_settings ───────────────────────────────────────────────
ALTER TABLE system_settings ENABLE ROW LEVEL SECURITY;

-- Solo admins de la empresa pueden leer/escribir su configuración
CREATE POLICY "company_admin_system_settings" ON system_settings
  USING (
    company_id = get_my_company_id()
    AND jwt_role() IN ('admin', 'root')
  );

-- Service role bypassa RLS (el backend Python usa service_role)
