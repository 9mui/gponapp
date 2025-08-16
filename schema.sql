
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS olts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hostname TEXT NOT NULL,
  ip       TEXT NOT NULL UNIQUE,
  community TEXT NOT NULL,
  vendor    TEXT NOT NULL DEFAULT 'bdcom'
);

CREATE TABLE IF NOT EXISTS ponports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  olt_ip   TEXT NOT NULL,
  ifindex  TEXT NOT NULL,
  name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gpon (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  olt_ip  TEXT NOT NULL,
  portonu TEXT NOT NULL,    -- ifIndex
  idonu   TEXT NOT NULL,    -- ONU ID
  snonu   TEXT NOT NULL     -- Serial (uppercase)
);
CREATE UNIQUE INDEX IF NOT EXISTS gpon_uq ON gpon(olt_ip, portonu, idonu);

CREATE TABLE IF NOT EXISTS onu_notes (
  sn_norm   TEXT PRIMARY KEY,
  note      TEXT NOT NULL DEFAULT '',
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gpon_olt_port_id ON gpon(olt_ip, portonu, idonu);
CREATE INDEX IF NOT EXISTS idx_ponports_olt_if ON ponports(olt_ip, ifindex);