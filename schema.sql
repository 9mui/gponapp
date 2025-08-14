
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
  snonu   TEXT NOT NULL,    -- Serial (uppercase)
  comment1 TEXT,
  comment2 TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS gpon_uq ON gpon(olt_ip, portonu, idonu);

