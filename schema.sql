-- GPON Monitoring Database Schema
-- Оптимизированная схема для высокопроизводительного мониторинга GPON сетей

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-64000;  -- 64MB cache
PRAGMA temp_store=MEMORY;
PRAGMA mmap_size=268435456;  -- 256MB mmap

-- Таблица OLT устройств
CREATE TABLE IF NOT EXISTS olts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hostname TEXT NOT NULL,
  ip       TEXT NOT NULL UNIQUE,
  community TEXT NOT NULL,
  vendor    TEXT NOT NULL DEFAULT 'bdcom',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Таблица PON портов
CREATE TABLE IF NOT EXISTS ponports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  olt_ip   TEXT NOT NULL,
  ifindex  TEXT NOT NULL,
  name     TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (olt_ip) REFERENCES olts(ip) ON DELETE CASCADE
);

-- Таблица ONU устройств
CREATE TABLE IF NOT EXISTS gpon (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  olt_ip  TEXT NOT NULL,
  portonu TEXT NOT NULL,    -- ifIndex
  idonu   TEXT NOT NULL,    -- ONU ID
  snonu   TEXT NOT NULL,    -- Serial (uppercase)
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (olt_ip) REFERENCES olts(ip) ON DELETE CASCADE
);

-- Таблица для отслеживания появления новых ONU
CREATE TABLE IF NOT EXISTS onu_seen (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  olt_ip TEXT NOT NULL,
  portonu TEXT NOT NULL,
  idonu TEXT NOT NULL,
  snonu TEXT NOT NULL,
  first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (olt_ip) REFERENCES olts(ip) ON DELETE CASCADE
);

-- Индексы для оптимизации производительности
CREATE UNIQUE INDEX IF NOT EXISTS gpon_uq ON gpon(olt_ip, portonu, idonu);
CREATE UNIQUE INDEX IF NOT EXISTS onu_seen_uq ON onu_seen(olt_ip, portonu, idonu);
CREATE INDEX IF NOT EXISTS idx_gpon_olt_ip ON gpon(olt_ip);
CREATE INDEX IF NOT EXISTS idx_gpon_snonu ON gpon(snonu);
CREATE INDEX IF NOT EXISTS idx_ponports_olt_ip ON ponports(olt_ip);
CREATE INDEX IF NOT EXISTS idx_onu_seen_first_seen ON onu_seen(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_onu_seen_last_seen ON onu_seen(last_seen DESC);

-- Нормализованный индекс для поиска по серийному номеру (без пробелов, верхний регистр)
CREATE UNIQUE INDEX IF NOT EXISTS ux_gpon_sn_norm ON gpon(olt_ip, REPLACE(UPPER(snonu),' ',''));
CREATE UNIQUE INDEX IF NOT EXISTS ux_onu_seen_sn_norm ON onu_seen(olt_ip, REPLACE(UPPER(snonu),' ',''));

-- Триггеры для автоматического обновления updated_at
CREATE TRIGGER IF NOT EXISTS update_olts_timestamp 
    AFTER UPDATE ON olts
    BEGIN
        UPDATE olts SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
    END;

CREATE TRIGGER IF NOT EXISTS update_gpon_timestamp 
    AFTER UPDATE ON gpon
    BEGIN
        UPDATE gpon SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
    END;

CREATE TRIGGER IF NOT EXISTS update_onu_seen_last_seen 
    AFTER UPDATE ON onu_seen
    BEGIN
        UPDATE onu_seen SET last_seen = CURRENT_TIMESTAMP WHERE id = NEW.id;
    END;

