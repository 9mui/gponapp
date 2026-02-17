# gpon_turon

Clean production-oriented GPON project (BDCOM) for ONU search, OLT/port visibility, and basic SNMP operations.

This file is written as context for a fresh ChatGPT session, so a new assistant can quickly understand architecture, logic, and safe change rules.

## 1. Project Goal

NOC/support web app that:
- stores OLT inventory,
- refreshes OLT ports and ONU bindings via SNMP,
- searches ONU by SN,
- shows ONU and OLT operational state,
- tracks recently discovered ONUs,
- provides safe operational actions (refresh, port bounce, ONU reboot).

Priority: stability and predictable behavior.

## 2. Stack and Layout

- Backend: Flask
- Database: SQLite
- SNMP: `snmpbulkwalk`, `snmpset`
- UI: Jinja2 templates + shared CSS

Main layout:
- `src/gpon_turon/app.py` — app factory, background auto-refresh, Jinja filters
- `src/gpon_turon/routes/` — HTTP routes (`olts.py`, `onu.py`)
- `src/gpon_turon/services/` — business logic (refresh, SNMP, reboot)
- `src/gpon_turon/repositories/` — SQL access
- `src/gpon_turon/db.py` — DB init + runtime migrations
- `templates/` — pages
- `static/` — CSS/images
- `schema.sql` — base schema
- `run.py` — entrypoint

Architecture rule: `routes -> services -> repositories`.

## 3. Current Features

### 3.1 Home `/`
- Add/delete OLT
- `Refresh all OLT` button
- `New ONU` button
- OLT table (Hostname, IP, Vendor, Last refresh, actions)

### 3.2 OLT page `/olt/<ip>`
- GPON ports table with ONU count
- `Refresh data` (single OLT refresh)
- `OLT info` button
- Per-port actions:
  - `Open`
  - `Reboot port` (SNMP down/up)

### 3.3 Port page `/olt/<ip>/port/<ifindex>`
- ONU list on selected port (paged)
- Link to ONU details

### 3.4 ONU page `/onu/sn/<sn>`
- OLT IP, Port, Status, LAN status, Distance, RX/TX, ONU vendor
- Last down reason
- For OFFLINE: `Last online`
- `Reboot ONU` red button with confirmation

### 3.5 New ONUs page `/onus/new`
- Shows last 50 newly discovered ONUs
- Columns: number, SN, connected time, OLT IP, port
- SN links to ONU page, OLT IP links to OLT page
- Retention: an item remains until pushed out by the 51st newer item

### 3.6 OLT info page `/olt/<ip>/info`
Shows:
- IP
- Vendor
- Model
- Firmware version
- Memory
- CPU
- Temperature

Unavailable SNMP values are shown as `-`.

## 4. Core Data Logic

### 4.1 OLT refresh flow
Implemented in `OltService.refresh_olt`:
1. Read `ifName` and GPON bind via SNMP.
2. Sync `ponports` (diff insert/update/delete).
3. Sync `gpon` (diff insert/delete).
4. Remove cross-OLT duplicates by SN (ONU moved to another OLT/port).
5. Update `last_refresh_at`.

### 4.2 Recent new ONU tracking
When new SN rows are inserted into `gpon`:
- check whether SN was globally known before,
- if not, insert into `recent_new_onu`,
- keep only latest 50 records.

### 4.3 Real `Last online`
Important distinction:
- `last_seen` means “seen in cache”, not real online state.
- Real online history is stored in `onu_seen.last_online`.
- `last_online` is updated only when SNMP status is `ONLINE`.
- ONU page uses `last_online` for OFFLINE ONUs.

This prevents showing “last auto-refresh time” as “last online”.

### 4.4 Timezone
All UI timestamps are rendered with `tz_tashkent` filter:
- timezone: `Asia/Tashkent` (GMT+5)
- DB timestamps are treated as UTC source.

## 5. Database (Main Tables)

- `olts` — OLT inventory (`hostname/ip/community/vendor/last_refresh_at`)
- `ponports` — OLT ports (`ifindex`, `name`)
- `gpon` — ONU bindings (`olt_ip`, `portonu`, `idonu`, `snonu`)
- `onu_seen` — SN observation history:
  - `first_seen`
  - `last_seen`
  - `last_online`
  - `status`
- `recent_new_onu` — last 50 new ONUs

Runtime migrations in `db.py` ensure backward compatibility for existing DB files.

## 6. SNMP OIDs Used

BDCOM/GPON main OIDs:
- GPON bind SN: `1.3.6.1.4.1.3320.10.2.6.1.3`
- ONU SN table: `1.3.6.1.4.1.3320.10.3.1.1.4`
- ONU status: `1.3.6.1.4.1.3320.10.3.3.1.4`
- ONU RX/TX: `...10.3.4.1.2`, `...10.3.4.1.3`
- ONU distance: `...10.3.1.1.33`
- ONU last down reason: `...10.3.1.1.35`
- ONU reboot: `...10.3.2.1.4.<globIdx>`
- Port bounce (ifAdminStatus): `1.3.6.1.2.1.2.2.1.7.<ifIndex>`
- OLT sysDescr/sysName: `1.3.6.1.2.1.1.1.0`, `1.3.6.1.2.1.1.5.0`
- OLT CPU/MEM/TEMP:
  - `1.3.6.1.4.1.3320.9.109.1.1.1.1.0`
  - `1.3.6.1.4.1.3320.9.48.1.0`
  - `1.3.6.1.4.1.3320.9.181.1.1.7.0`

## 7. Run

```bash
cd gpon_turon
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
export PYTHONPATH=src
python run.py
```

Defaults:
- host: `0.0.0.0`
- port: `5001`

## 8. Auto Refresh

Background thread in `app.py`.
Configured via env:
- `AUTO_REFRESH_ENABLED=true/false`
- `AUTO_REFRESH_INTERVAL_MINUTES=15`

Global lock prevents overlapping full refresh cycles.

## 9. Safe Change Rules (for a new ChatGPT session)

1. Keep layered architecture (`route/service/repository`).
2. Keep unified UI style.
3. Keep SNMP/OID logic inside services, not routes.
4. Keep lock protection for mass refresh.
5. Do not use `last_seen` as “last online” for offline ONU logic.
6. Return `-` for unsupported SNMP values, avoid hard failures.
7. Logic fixes first, cosmetic changes second.

## 10. Intentionally Out of Scope (current stage)

- Multi-vendor support (current target vendor: BDCOM)
- CSV export
- Advanced auth/roles
- Heavy frontend JS

## 11. Route Quick List

- `GET /` — home
- `POST /olts/add` — add OLT
- `POST /olts/<id>/delete` — delete OLT
- `POST /olts/refresh-all` — refresh all OLT
- `GET /olt/<ip>` — OLT page
- `POST /olt/<ip>/refresh` — refresh one OLT
- `GET /olt/<ip>/info` — OLT info page
- `POST /olt/<ip>/port/<ifindex>/bounce` — reboot port
- `GET /olt/<ip>/port/<ifindex>` — ONU list on port
- `POST /search` — ONU search
- `GET /onu/sn/<sn>` — ONU page
- `POST /onu/sn/<sn>/reboot` — reboot ONU
- `GET /onus/new` — recent new ONUs
- `GET /health` — healthcheck

## 12. Status

Project is in working state and serves as a clean base for further development.
