import re, csv, io, time, logging
import os
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, redirect, url_for, abort, Response, flash, jsonify
from models import db, ensure_db

# -------------------- Логирование --------------------
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('gponapp.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# -------------------- SNMP Caching --------------------
from functools import lru_cache
from time import time

# Simple time-based cache for SNMP responses
snmp_cache = {}
CACHE_TTL = 30  # 30 seconds cache TTL

def cached_snmpwalk(ip: str, community: str, oid: str, ttl: int = CACHE_TTL):
    """Cached SNMP walk with TTL support"""
    cache_key = f"{ip}:{community}:{oid}"
    current_time = time()
    
    # Check if we have valid cached data
    if cache_key in snmp_cache:
        cached_data, cached_time = snmp_cache[cache_key]
        if current_time - cached_time < ttl:
            return cached_data
    
    # Fetch fresh data
    try:
        result = snmpwalk(ip, community, oid)
        snmp_cache[cache_key] = (result, current_time)
        
        # Clean old cache entries (simple cleanup)
        if len(snmp_cache) > 1000:  # Limit cache size
            old_keys = [k for k, (_, t) in snmp_cache.items() if current_time - t > ttl * 2]
            for old_key in old_keys[:100]:  # Remove oldest 100 entries
                snmp_cache.pop(old_key, None)
        
        return result
    except Exception as e:
        logger.error(f"SNMP error for {ip}:{oid} - {e}")
        return None

# -------------------- SNMP импорты --------------------
from snmp import (
    snmpwalk, snmpset, first_int, first_str,
    OID_IFNAME, OID_IF_DESCR, OID_IF_ALIAS, OID_IF_OPER_STATUS, OID_IF_ADMIN_STATUS,
    OID_IF_IN_5M_BIT, OID_IF_OUT_5M_BIT,
    OID_GPON_BIND_SN, OID_GPON_STATUS, OID_GPON_ONU_RX, OID_GPON_ONU_TX,
    OID_GPON_ONU_VENDOR, OID_GPON_ONU_SW_A, OID_GPON_ONU_SW_B, OID_GPON_ONU_DIST, OID_GPON_ONU_LASTDN,
    OID_GPON_ONU_SN_TAB, OID_PON_PORT_TX, OID_PON_PORT_RX,
    OID_SYS_NAME, OID_SYS_LOCATION, OID_SYS_CONTACT, OID_SYS_DESCR, OID_SYS_TIME_STR,
    OID_CPU_USAGE, OID_MEM_USAGE, OID_TEMP_BOARD, OID_OLT_REBOOT,
    parse_ifname, parse_gpon_bind, find_glob_idx_by_sn, OFFLINE_REASON, OID_IF_LAST_CHANGE,
    get_int, get_str, OID_LAN_STATUS_4, OID_LAN_STATUS_1
)

# -------------------- Flask/App --------------------
app = Flask(__name__)
app.secret_key = "gponapp-secret"
ensure_db()

# Часовой пояс оператора (UTC+05:00)
TZ_LOCAL = timezone(timedelta(hours=5))

# -------------------- Планировщик --------------------
# -------------------- Планировщик --------------------
scheduler = None  # глобальная ссылка, чтобы не стартовать второй раз

def start_scheduler_once():
    """Запускает APScheduler один раз (без дубля в дев-режиме)."""
    global scheduler
    if scheduler is not None:
        return
    is_main = os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug
    if not is_main:
        return

    # ВАЖНО: job_defaults делает задания устойчивее
    s = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,            # склеивать накопившиеся тики в один запуск
            "max_instances": 1,          # не запускать параллельно
            "misfire_grace_time": 300,   # до 5 минут «грейс-период» при просыпании
        },
    )

    # Базовый опрос каждые 5 минут
    s.add_job(
        poll_all_olts,
        IntervalTrigger(minutes=5),
        id="poll_all_olts",
        replace_existing=True,
        next_run_time=datetime.utcnow(),  # стартовать сразу
    )

    # Принудительное «как кнопка Обновить» каждые 30 минут (резерв)
    s.add_job(
        poll_all_olts,  # то же самое, что ручная /olt/<ip>/refresh — внутри refresh_olt_cache(...)
        IntervalTrigger(minutes=30),
        id="force_refresh_all_olts",
        replace_existing=True,
        next_run_time=datetime.utcnow(),  # и эта тоже стартует сразу
    )

    s.start()
    scheduler = s
# -------------------- Утилиты --------------------
HEX16_RE = re.compile(r"[0-9A-F]{16}")

def norm_sn(s: str) -> str:
    s = (s or "").strip().upper()
    if ":" in s and len(s.split(":", 1)[1]) >= 16:
        s = s.split(":", 1)[1]
    s = re.sub(r"[^0-9A-F]", "", s)
    m = HEX16_RE.search(s)
    return m.group(0) if m else s[-16:]

def sn_to_norm(sn: str) -> str:
    return norm_sn(sn)

def get_onu_note(conn, sn: str) -> str:
    row = conn.execute("SELECT note FROM onu_notes WHERE sn_norm = ?", (sn_to_norm(sn),)).fetchone()
    return row[0] if row else ""

def upsert_onu_note(conn, sn: str, note: str):
    conn.execute("""
        INSERT INTO onu_notes(sn_norm, note)
        VALUES(?, ?)
        ON CONFLICT(sn_norm) DO UPDATE SET note=excluded.note, updated_at=CURRENT_TIMESTAMP
    """, (sn_to_norm(sn), note.strip()))

def ticks_to_hms(ticks: int | None) -> str:
    if ticks is None: return "-"
    sec = ticks // 100
    d = sec // 86400; h = (sec % 86400) // 3600; m = (sec % 3600) // 60; s = sec % 60
    return f"{d}d {h:02}:{m:02}:{s:02}"

def get_sys_uptime_ticks(ip: str, community: str) -> int | None:
    for oid in ("1.3.6.1.2.1.1.3.0", "1.3.6.1.2.1.25.1.1.0"):
        lines = snmpwalk(ip, community, oid) or []
        for ln in lines:
            m = re.search(r"Timeticks:\s*\((\d+)\)", ln)
            if m: return int(m.group(1))
    return None

def get_if_last_change_ticks(ip: str, community: str, ifindex: int | str) -> int | None:
    lines = snmpwalk(ip, community, f"{OID_IF_LAST_CHANGE}.{ifindex}") or []
    for ln in lines:
        m = re.search(r"Timeticks:\s*\((\d+)\)", ln)
        if m: return int(m.group(1))
        m = re.search(r"=\s*(\d+)\s*$", ln)
        if m: return int(m.group(1))
    return None

def last_change_to_local_dt(sys_uptime_ticks: int | None, last_change_ticks: int | None):
    if sys_uptime_ticks is None or last_change_ticks is None: return None
    if last_change_ticks > sys_uptime_ticks: return None
    delta_sec = (sys_uptime_ticks - last_change_ticks) / 100.0
    return datetime.now(TZ_LOCAL) - timedelta(seconds=delta_sec)

def get_cpu_percent(ip: str, community: str) -> int | None:
    lines = snmpwalk(ip, community, "1.3.6.1.2.1.25.3.3.1.2")
    vals = []
    for ln in lines or []:
        m = re.search(r"(?:INTEGER|Gauge32):\s*(\d+)", ln)
        if m: vals.append(int(m.group(1)))
    return round(sum(vals) / len(vals)) if vals else None

def meters_from_dm(raw):
    try:
        return None if raw is None else round(float(raw) / 10.0, 1)
    except Exception:
        return None

# --- OID ---
OID_ONU_RESET = "1.3.6.1.4.1.3320.10.3.2.1.4"

# -------------------- Кэш OLT/ONU --------------------
def refresh_olt_cache(ip: str) -> bool:
    """Optimized cache refresh with bulk operations and error handling"""
    try:
        with db() as conn:
            got = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()
            if not got: 
                logger.warning(f"OLT {ip} not found in database")
                return False
            community = got[0]

            logger.info(f"Starting cache refresh for OLT {ip}")
            
            # ifName -> ponports (optimized with cached SNMP and bulk operations)
            ifs = parse_ifname(cached_snmpwalk(ip, community, OID_IFNAME, ttl=60) or [])  # Cache for 1 minute
            if ifs:
                conn.execute("DELETE FROM ponports WHERE olt_ip = ?", (ip,))
                # Bulk insert for better performance
                conn.executemany(
                    "INSERT INTO ponports(olt_ip, ifindex, name) VALUES(?,?,?)", 
                    [(ip, ifi, name) for ifi, name in ifs]
                )
                logger.debug(f"Updated {len(ifs)} PON ports for OLT {ip}")

            # GPON bindings optimization with cached SNMP and batch processing
            binds = parse_gpon_bind(cached_snmpwalk(ip, community, OID_GPON_BIND_SN, ttl=60) or [])  # Cache for 1 minute
            sns_to_clean = []  # Initialize here to avoid unbound variable
            if binds:
                conn.execute("DELETE FROM gpon WHERE olt_ip = ?", (ip,))
                
                # Prepare data for bulk operations
                gpon_data = []
                onu_seen_data = []
                sns_to_clean = []  # Track SNs found on this OLT
                
                for ifi, onuid, sn in binds:
                    sn_norm = norm_sn(sn)
                    gpon_data.append((ip, ifi, onuid, sn))
                    onu_seen_data.append((sn_norm,))
                    sns_to_clean.append(sn_norm)
                
                # Clean up duplicate entries from other OLTs for these SNs
                if sns_to_clean:
                    placeholders = ','.join(['?' for _ in sns_to_clean])
                    query = f"""DELETE FROM gpon 
                                WHERE REPLACE(UPPER(snonu),' ','') IN ({placeholders}) 
                                AND olt_ip != ?"""
                    conn.execute(query, sns_to_clean + [ip])
                    logger.debug(f"Cleaned duplicate entries for {len(sns_to_clean)} ONUs from other OLTs")
                
                # Bulk insert GPON data
                conn.executemany(
                    "INSERT INTO gpon(olt_ip, portonu, idonu, snonu) VALUES(?,?,?,?)",
                    gpon_data
                )
                
                # Simple bulk upsert ONU seen data - mark all as potentially online
                # since they were found in GPON bindings
                current_time = datetime.now().isoformat()
                for sn_norm in sns_to_clean:
                    conn.execute(
                        """INSERT INTO onu_seen (sn_norm, status, last_online)
                           VALUES (?, 3, ?)
                           ON CONFLICT(sn_norm) DO UPDATE SET 
                           last_seen = CURRENT_TIMESTAMP,
                           status = 3,
                           last_online = ?""",
                        (sn_norm, current_time, current_time)
                    )
                
                logger.info(f"Updated {len(binds)} GPON bindings for OLT {ip}")
            
            # Mark ONUs that are no longer in GPON bindings as potentially offline
            # This is a cleanup step to detect disconnected ONUs
            try:
                # Get all ONUs that were previously seen on this OLT but are no longer in bindings
                existing_onus = conn.execute(
                    """SELECT DISTINCT REPLACE(UPPER(snonu),' ','') 
                       FROM gpon 
                       WHERE olt_ip = ?""", 
                    (ip,)
                ).fetchall()
                
                existing_sns = {row[0] for row in existing_onus}
                current_sns = set(sns_to_clean) if sns_to_clean else set()
                missing_sns = existing_sns - current_sns
                
                if missing_sns:
                    # Mark missing ONUs with status 0 (offline) but don't update last_online
                    for missing_sn in missing_sns:
                        conn.execute(
                            """UPDATE onu_seen 
                               SET status = 0, last_seen = CURRENT_TIMESTAMP 
                               WHERE sn_norm = ? AND status != 0""",
                            (missing_sn,)
                        )
                    logger.debug(f"Marked {len(missing_sns)} ONUs as offline on OLT {ip}")
            except Exception as e:
                logger.warning(f"Error updating offline status for OLT {ip}: {e}")
            
            return True
            
    except Exception as e:
        logger.error(f"Error refreshing cache for OLT {ip}: {e}")
        return False

def poll_all_olts():
    """Optimized parallel polling of all OLTs with detailed logging"""
    start_time = datetime.now()
    success_count = 0
    error_count = 0
    
    try:
        with db() as conn:
            olts = conn.execute("SELECT ip, hostname FROM olts ORDER BY id").fetchall()
        
        if not olts:
            logger.info("No OLTs configured for polling")
            return
            
        logger.info(f"Starting parallel polling of {len(olts)} OLTs")
        
        def poll_single_olt(olt_data):
            ip, hostname = olt_data
            try:
                if refresh_olt_cache(ip):
                    logger.debug(f"Successfully polled OLT {hostname} ({ip})")
                    return True
                else:
                    logger.warning(f"Failed to poll OLT {hostname} ({ip})")
                    return False
            except Exception as e:
                logger.error(f"Error polling OLT {hostname} ({ip}): {e}")
                return False
        
        # Use ThreadPoolExecutor for parallel processing
        max_workers = min(8, max(1, len(olts)))  # Limit concurrent connections
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(poll_single_olt, olt) for olt in olts]
            
            for future in as_completed(futures):
                try:
                    if future.result():
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    logger.error(f"Future execution error: {e}")
        
        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"Polling completed: {success_count} successful, {error_count} failed, {duration:.2f}s total")
        
    except Exception as e:
        logger.error(f"Critical error in poll_all_olts: {e}")
        
    # Log performance metrics
    if success_count + error_count > 0:
        success_rate = (success_count / (success_count + error_count)) * 100
        logger.info(f"Polling success rate: {success_rate:.1f}%")

def scan_sn_on_olt(ip: str, community: str, sn_hex16: str):
    try:
        lines = snmpwalk(ip, community, OID_GPON_BIND_SN)
    except Exception:
        return None
    target = norm_sn(sn_hex16)
    for ifi, onuid, raw_sn in parse_gpon_bind(lines):
        if norm_sn(raw_sn) == target:
            return (ifi, onuid)
    return None

def resolve_onu_location(sn_hex16: str):
    sn = norm_sn(sn_hex16)
    with db() as conn:
        olts = conn.execute("SELECT ip, community FROM olts ORDER BY id").fetchall()

    def _scan(ipa_comm):
        ip, comm = ipa_comm
        found = scan_sn_on_olt(ip, comm, sn)
        return (ip, comm, found)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(olts)))) as ex:
        futs = {ex.submit(_scan, oc): oc for oc in olts}
        for fut in as_completed(futs):
            ip, comm, found = fut.result()
            if found:
                port_if, onuid = found
                with db() as conn:
                    # Clean up ALL entries for this SN from any OLT
                    conn.execute("DELETE FROM gpon WHERE REPLACE(UPPER(snonu),' ','') = ?", (sn,))
                    # Insert the current location
                    conn.execute("INSERT INTO gpon(olt_ip, portonu, idonu, snonu) VALUES(?,?,?,?)",
                                 (ip, port_if, onuid, sn))
                    logger.info(f"Updated ONU {sn} location to OLT {ip} port {port_if}:{onuid}")
                return ip, comm, port_if, onuid
    return None

# -------------------- Новые ONU --------------------
def _to_local_str(dt_str):
    """Строку SQLite 'YYYY-MM-DD HH:MM:SS' (UTC) -> локальная строка в +05:00."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)          # naive UTC
        dt = dt.replace(tzinfo=timezone.utc)         # помечаем как UTC
        return dt.astimezone(TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str

@app.get("/onus/recent")
def recent_onus_page():
    with db() as conn:
        rows = conn.execute("""
            SELECT
              s.sn_norm,
              s.first_seen,
              g.olt_ip
            FROM onu_seen s
            LEFT JOIN gpon g
              ON REPLACE(UPPER(g.snonu),' ','') = s.sn_norm
            ORDER BY s.first_seen DESC
            LIMIT 20
        """).fetchall()

    rows_loc = [(r[0], _to_local_str(r[1]), r[2]) for r in rows]
    return render_template("recent_onus.html", rows=rows_loc)

@app.get("/api/onus/recent")
def recent_onus_api():
    with db() as conn:
        rows = conn.execute("""
            SELECT
              s.sn_norm,
              s.first_seen,
              g.olt_ip
            FROM onu_seen s
            LEFT JOIN gpon g
              ON REPLACE(UPPER(g.snonu),' ','') = s.sn_norm
            ORDER BY s.first_seen DESC
            LIMIT 20
        """).fetchall()

    data = []
    for sn_norm, first_seen, olt_ip in rows:
        data.append({
            "sn": sn_norm,
            "first_seen": _to_local_str(first_seen),
            "olt_ip": olt_ip
        })
    return jsonify(data)

# -------------------- Главная/поиск/OLTs --------------------
@app.get("/")
def home():
    with db() as conn:
        rs = conn.execute("SELECT id, hostname, ip, vendor FROM olts ORDER BY id").fetchall()
    rows = [dict(id=r[0], hostname=r[1], ip=r[2], vendor=r[3]) for r in rs]
    return render_template("index.html", olts=rows)

@app.post("/search")
def search():
    q = (request.form.get("q") or "").strip()
    if not q:
        return redirect(request.referrer or url_for("home"))
    hexs = re.findall(r"[0-9A-Fa-f]", q)
    sn = "".join(hexs[-16:]).upper() if len(hexs) >= 16 else None
    if not sn:
        flash("Введите корректный SN (16 HEX символов)", "info")
        return redirect(request.referrer or url_for("home"))

    with db() as conn:
        row = conn.execute("""
            SELECT olt_ip FROM gpon WHERE REPLACE(UPPER(snonu),' ','') = ? LIMIT 1
        """, (sn,)).fetchone()
    if row:
        return redirect(url_for("onu_by_sn", sn=sn))

    resolved = resolve_onu_location(sn)
    if resolved:
        return redirect(url_for("onu_by_sn", sn=sn))

    return render_template("not_found.html", q=sn)

@app.get("/olts")
def list_olts():
    with db() as conn:
        rows = conn.execute("SELECT id, hostname, ip, community, vendor FROM olts ORDER BY id").fetchall()
    return render_template("olts.html", rows=rows)

@app.post("/olts/add")
def add_olt():
    hostname = request.form.get("hostname","").strip()
    ip       = request.form.get("ip","").strip()
    comm     = request.form.get("community","private").strip()
    vendor   = (request.form.get("vendor","bdcom") or "bdcom").lower()
    if not hostname or not ip:
        flash("Hostname и IP обязательны", "error")
        return redirect(request.referrer or url_for("home"))
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO olts(hostname,ip,community,vendor) VALUES(?,?,?,?)",
                     (hostname, ip, comm, vendor))
    flash(f"OLT {hostname} ({ip}) добавлен", "info")
    return redirect(url_for("home"))

# -------------------- Страницы OLT --------------------
@app.get("/olt/<ip>")
def olt(ip):
    with db() as conn:
        row = conn.execute("SELECT hostname, community, vendor FROM olts WHERE ip = ?", (ip,)).fetchone()
        if not row: abort(404)
        hostname, community, vendor = row
        ports = conn.execute("""
            SELECT p.ifindex, p.name,
                   COALESCE((SELECT COUNT(*) FROM gpon g WHERE g.olt_ip=p.olt_ip AND g.portonu=p.ifindex),0) AS cnt
            FROM ponports p
            WHERE p.olt_ip = ?
              AND p.name LIKE 'GPON%/%'
              AND p.name NOT LIKE '%:%'
            ORDER BY CAST(p.ifindex AS INT)
        """, (ip,)).fetchall()
    return render_template("olt_ports.html", ip=ip, hostname=hostname, ports=ports)

@app.post("/olt/<ip>/refresh")
def olt_refresh(ip):
    refresh_olt_cache(ip)
    flash("Кэш портов и привязок обновлён", "info")
    return redirect(url_for("olt", ip=ip))

@app.get("/olt/<ip>/device")
def olt_device(ip):
    with db() as conn:
        row = conn.execute("SELECT hostname, community FROM olts WHERE ip = ?", (ip,)).fetchone()
        if not row: abort(404)
        hostname, community = row
    sys_name   = first_str(snmpwalk(ip, community, OID_SYS_NAME))
    sys_loc    = first_str(snmpwalk(ip, community, OID_SYS_LOCATION))
    sys_cont   = first_str(snmpwalk(ip, community, OID_SYS_CONTACT))
    sys_descr  = first_str(snmpwalk(ip, community, OID_SYS_DESCR))
    sys_time   = first_str(snmpwalk(ip, community, OID_SYS_TIME_STR))
    uptime     = get_sys_uptime_ticks(ip, community)
    cpu        = get_cpu_percent(ip, community)
    mem        = first_int(snmpwalk(ip, community, OID_MEM_USAGE))
    temp       = first_int(snmpwalk(ip, community, OID_TEMP_BOARD))
    return render_template("olt_device.html",
                           ip=ip, hostname=hostname,
                           sys_name=sys_name, sys_loc=sys_loc, sys_cont=sys_cont,
                           sys_descr=sys_descr, sys_time=sys_time,
                           uptime_hms=ticks_to_hms(uptime),
                           cpu=cpu, mem=mem, temp=temp)

# -------------------- Uplinks (LAN) --------------------
@app.get("/olt/<ip>/uplinks")
def olt_uplinks(ip):
    with db() as conn:
        row = conn.execute("SELECT hostname, community FROM olts WHERE ip = ?", (ip,)).fetchone()
        if not row: abort(404)
        hostname, community = row

    names  = snmpwalk(ip, community, OID_IFNAME)
    descrs = snmpwalk(ip, community, OID_IF_DESCR)
    alias  = snmpwalk(ip, community, OID_IF_ALIAS)
    status = snmpwalk(ip, community, OID_IF_OPER_STATUS)

    def parse_map(pattern, lines):
        m = {}
        for ln in lines:
            r = re.search(pattern, ln)
            if r: m[r.group(1)] = r.group(2)
        return m

    map_name  = parse_map(r"\.(\d+)\s*=\s*STRING:\s*\"([^\"]+)\"", names)
    map_descr = parse_map(r"\.(\d+)\s*=\s*STRING:\s*\"([^\"]*)\"", descrs)
    map_alias = parse_map(r"\.(\d+)\s*=\s*STRING:\s*\"([^\"]*)\"", alias)
    map_oper  = {}
    for ln in status:
        m = re.search(r"\.(\d+)\s*=\s*INTEGER:\s*(\d+)", ln)
        if m: map_oper[m.group(1)] = int(m.group(2))

    rows = []
    for ifi, name in map_name.items():
        if name.startswith("GPON"):
            continue
        in5  = get_int(ip, community, f"{OID_IF_IN_5M_BIT}.{ifi}")
        out5 = get_int(ip, community, f"{OID_IF_OUT_5M_BIT}.{ifi}")
        rows.append({
            "ifindex": ifi,
            "name": name,
            "descr": map_descr.get(ifi) or "",
            "alias": map_alias.get(ifi) or "",
            "oper": map_oper.get(ifi),
            "in5": in5, "out5": out5
        })
    rows.sort(key=lambda r: int(r["ifindex"]))
    return render_template("uplinks.html", ip=ip, hostname=hostname, rows=rows)

# -------------------- Port (PON) --------------------
@app.get("/olt/<ip>/port/<ifindex>")
def port(ip, ifindex):
    with db() as conn:
        row = conn.execute("SELECT name FROM ponports WHERE olt_ip = ? AND ifindex = ?", (ip, ifindex)).fetchone()
        if not row: abort(404)
        port_name = row[0]
        onus = conn.execute("""
            SELECT g.snonu, g.idonu, COALESCE(n.note, '')
            FROM gpon g
            LEFT JOIN onu_notes n
              ON REPLACE(UPPER(g.snonu),' ','') = n.sn_norm
            WHERE g.olt_ip = ? AND g.portonu = ?
            ORDER BY CAST(g.idonu AS INT)
        """, (ip, ifindex)).fetchall()
        comm = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()[0]
    tx = get_int(ip, comm, f"{OID_PON_PORT_TX}.{ifindex}")
    rx = get_int(ip, comm, f"{OID_PON_PORT_RX}.{ifindex}")
    stat = get_int(ip, comm, f"{OID_IF_OPER_STATUS}.{ifindex}")
    def dbm(x): return None if x is None else x/10.0
    return render_template("port_onus.html",
                           ip=ip, port_name=port_name, ifindex=ifindex, onus=onus,
                           pon_tx=dbm(tx), pon_rx=dbm(rx), port_status=stat)

@app.get("/olt/<ip>/port/<ifindex>/export.csv")
def port_export_csv(ip, ifindex):
    with db() as conn:
        rows = conn.execute(
            "SELECT snonu, idonu FROM gpon WHERE olt_ip = ? AND portonu = ? ORDER BY CAST(idonu AS INT)",
            (ip, ifindex)
        ).fetchall()
        pname = conn.execute("SELECT name FROM ponports WHERE olt_ip = ? AND ifindex = ?", (ip, ifindex)).fetchone()
        pname = (pname[0] if pname else ifindex).replace("/", "_").replace(":", "_")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SN", "ONU_ID", "PON_ifIndex"])
    for sn, onuid in rows:
        w.writerow([sn, onuid, ifindex])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{ip}_{pname}.csv"'})

@app.post("/olt/<ip>/port/<ifindex>/bounce")
def port_bounce(ip, ifindex):
    with db() as conn:
        row = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()
        if not row: abort(404)
        community = row[0]
    ok1 = snmpset(ip, community, f"{OID_IF_ADMIN_STATUS}.{ifindex}", "i", "2")
    time.sleep(1)
    ok2 = snmpset(ip, community, f"{OID_IF_ADMIN_STATUS}.{ifindex}", "i", "1")
    flash("Порт отправлен в down→up" if (ok1 and ok2) else "Не удалось изменить ifAdminStatus", "info")
    return redirect(url_for("port", ip=ip, ifindex=ifindex))

@app.post("/olt/<ip>/reboot")
def olt_reboot(ip):
    with db() as conn:
        row = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()
        if not row: abort(404)
        community = row[0]
    ok = snmpset(ip, community, OID_OLT_REBOOT, "i", "1")
    flash("Команда перезагрузки отправлена" if ok else "Не удалось выполнить перезагрузку", "info")
    return redirect(url_for("olt", ip=ip))

@app.post("/olts/<ip>/delete")
def delete_olt(ip):
    with db() as conn:
        conn.execute("DELETE FROM gpon WHERE olt_ip = ?", (ip,))
        conn.execute("DELETE FROM ponports WHERE olt_ip = ?", (ip,))
        conn.execute("DELETE FROM olts WHERE ip = ?", (ip,))
    flash(f"OLT {ip} удалён", "info")
    return redirect(url_for("home"))

# -------------------- ONU page --------------------
@app.get("/onu/sn/<sn>")
def onu_by_sn(sn):
    sn = norm_sn(sn)
    with db() as conn:
        row = conn.execute("""
            SELECT olt_ip, portonu, idonu
            FROM gpon
            WHERE REPLACE(UPPER(snonu),' ','') = ?
            LIMIT 1
        """, (sn,)).fetchone()

    if not row:
        resolved = resolve_onu_location(sn)
        if not resolved:
            return render_template("not_found.html", q=sn)
        olt_ip, community, port_if, onuid_port = resolved
    else:
        olt_ip, port_if, onuid_port = row
        with db() as conn:
            community = conn.execute("SELECT community FROM olts WHERE ip = ?", (olt_ip,)).fetchone()[0]
        still_here = scan_sn_on_olt(olt_ip, community, sn)
        if not still_here:
            resolved = resolve_onu_location(sn)
            if not resolved:
                return render_template("not_found.html", q=sn)
            olt_ip, community, port_if, onuid_port = resolved

    with db() as conn:
        row_name = conn.execute("SELECT name FROM ponports WHERE olt_ip = ? AND ifindex = ?", (olt_ip, port_if)).fetchone()
        port_name_base = row_name[0] if row_name else f"ifIndex {port_if}"
        uni = conn.execute("SELECT ifindex, name FROM ponports WHERE olt_ip = ? AND name = ?",
                           (olt_ip, f"{port_name_base}:{onuid_port}")).fetchone()
        if uni:
            uni_ifindex, port_name_full = uni[0], uni[1]
        else:
            uni_ifindex, port_name_full = None, f"{port_name_base}:{onuid_port}"

    glob_idx = find_glob_idx_by_sn(olt_ip, community, sn)

    def first_non_none_int(base_oid: str):
        for idx in [glob_idx, uni_ifindex, f"{port_if}.{onuid_port}"]:
            if not idx: continue
            val = first_int(snmpwalk(olt_ip, community, f"{base_oid}.{idx}"))
            if val is not None: return val
        return None

    def first_non_none_get_int(base_oid: str):
        for idx in [glob_idx, uni_ifindex, f"{port_if}.{onuid_port}"]:
            if not idx: continue
            val = get_int(olt_ip, community, f"{base_oid}.{idx}")
            if val is not None: return val
        return None

    def first_non_none_get_str(base_oid: str):
        for idx in [glob_idx, uni_ifindex, f"{port_if}.{onuid_port}"]:
            if not idx: continue
            val = get_str(olt_ip, community, f"{base_oid}.{idx}")
            if val and "No Such" not in val: return val
        return None

    def first_non_none_str(base_oid: str):
        for idx in [glob_idx, uni_ifindex, f"{port_if}.{onuid_port}"]:
            if not idx: continue
            val = first_str(snmpwalk(olt_ip, community, f"{base_oid}.{idx}"))
            if val and "No Such" not in val: return val
        return None

    status   = first_non_none_get_int(OID_GPON_STATUS)
    rx_raw   = first_non_none_get_int(OID_GPON_ONU_RX)
    tx_raw   = first_non_none_get_int(OID_GPON_ONU_TX)
    vendor   = first_non_none_get_str(OID_GPON_ONU_VENDOR)
    dist_dm  = first_non_none_get_int(OID_GPON_ONU_DIST)
    lastdn   = first_non_none_get_int(OID_GPON_ONU_LASTDN)
    if rx_raw is None: rx_raw = first_non_none_int(OID_GPON_ONU_RX)
    if tx_raw is None: tx_raw = first_non_none_int(OID_GPON_ONU_TX)
    if vendor in (None, "", "No Such Instance", "No Such Object"):
        v2 = first_non_none_str(OID_GPON_ONU_VENDOR)
        if v2:
            v2 = re.sub(r'^(?:OCTET STRING|STRING|Hex-STRING):\s*', '', v2, flags=re.I).strip()
            if len(v2) >= 2 and v2[0] == '"' and v2[-1] == '"': v2 = v2[1:-1]
            vendor = v2

    def onu_lan_statuses(max_uni=4):
        results = []
        logger.debug(f"onu_lan_statuses glob_idx={glob_idx}, olt_ip={olt_ip}")
        for uni_port in range(1, max_uni + 1):
            val = None
            if glob_idx:
                oid1 = f"{OID_LAN_STATUS_4}.{glob_idx}.{uni_port}"
                oid2 = f"{OID_LAN_STATUS_4}.{uni_port}.{glob_idx}"
                val = first_int(snmpwalk(olt_ip, community, oid1))
                if val is None: val = first_int(snmpwalk(olt_ip, community, oid2))
                if val is None:
                    oid3 = f"{OID_LAN_STATUS_1}.{glob_idx}.{uni_port}"
                    oid4 = f"{OID_LAN_STATUS_1}.{uni_port}.{glob_idx}"
                    val = first_int(snmpwalk(olt_ip, community, oid3))
                    if val is None: val = first_int(snmpwalk(olt_ip, community, oid4))
            results.append({"uni": uni_port, "val": val, "text": "up" if val==1 else ("down" if val==2 else ("-" if val is None else str(val)))})
        return results

    lan_list = onu_lan_statuses()
    
    # Get last online time from database instead of calculating from SNMP
    last_online_str = None
    if status in (0, 1, 2):  # Only show for offline ONUs
        with db() as conn:
            last_online_row = conn.execute(
                "SELECT last_online FROM onu_seen WHERE sn_norm = ?", 
                (sn,)
            ).fetchone()
            if last_online_row and last_online_row[0]:
                last_online_str = _to_local_str(last_online_row[0])

    def dbm(x): return None if x is None else x/10.0
    distance = meters_from_dm(dist_dm)
    lastdn_txt = OFFLINE_REASON.get(lastdn, str(lastdn)) if lastdn is not None else "-"
    with db() as conn:
        note_txt = get_onu_note(conn, sn)

    return render_template("onu.html",
        sn=sn, olt_ip=olt_ip, port_if=port_if, onuid=onuid_port,
        port_name_full=port_name_full, uni_ifindex=uni_ifindex,
        status=status, rx=dbm(rx_raw), tx=dbm(tx_raw),
        vendor=vendor, distance=distance, last_down=lastdn_txt,
        lan_list=lan_list, glob_idx=glob_idx,
        note=note_txt, last_online_snmp=last_online_str
    )

@app.post("/onu/reboot/<sn>")
def onu_reboot(sn):
    sn_norm = norm_sn(sn)
    with db() as conn:
        row = conn.execute("""
            SELECT olt_ip FROM gpon
            WHERE REPLACE(UPPER(snonu),' ','') = ?
            LIMIT 1
        """, (sn_norm,)).fetchone()
        if not row:
            resolved = resolve_onu_location(sn_norm)
            if not resolved:
                flash(f"ONU {sn_norm} не найдена", "error")
                return redirect(url_for("home"))
            olt_ip, community, _, _ = resolved
        else:
            olt_ip = row[0]
            comm_row = conn.execute("SELECT community FROM olts WHERE ip = ?", (olt_ip,)).fetchone()
            if not comm_row:
                flash(f"Не найдено community для OLT {olt_ip}", "error")
                return redirect(url_for("home"))
            community = comm_row[0]

    glob_idx = find_glob_idx_by_sn(olt_ip, community, sn_norm)
    if not glob_idx:
        flash(f"Не удалось определить глобальный индекс ONU {sn_norm} на {olt_ip}", "error")
        return redirect(url_for("onu_by_sn", sn=sn_norm))

    ok = snmpset(olt_ip, community, f"{OID_ONU_RESET}.{glob_idx}", "i", "1")
    flash(
        f"Reset отправлен ONU {sn_norm} (index {glob_idx}) на {olt_ip}" if ok
        else f"Сбой SNMP SET для ONU {sn_norm} (index {glob_idx}) на {olt_ip}",
        "info" if ok else "error"
    )
    return redirect(url_for("onu_by_sn", sn=sn_norm))

@app.post("/onu/<sn>/note")
def save_onu_note(sn):
    note = request.form.get("note", "").strip()
    with db() as conn:
        upsert_onu_note(conn, sn, note)
    flash("Комментарий сохранён", "info")
    return redirect(request.referrer or url_for("onu_by_sn", sn=norm_sn(sn)))

# (Опционально) Лента последних по кэшу gpon:
@app.get("/recent-onu")
def recent_onu():
    with db() as conn:
        rows = conn.execute("""
            SELECT g.id, g.snonu, g.olt_ip, COALESCE(o.hostname, g.olt_ip) AS hostname
            FROM gpon g
            LEFT JOIN olts o ON o.ip = g.olt_ip
            ORDER BY g.id DESC
            LIMIT 20
        """).fetchall()
    items = [{
        "id": r[0],
        "sn": str(r[1]).upper(),
        "olt_ip": r[2],
        "hostname": r[3],
    } for r in rows]
    return render_template("recent_onu.html", items=items, title="Новые ONU", header="Новые ONU")

# -------------------- Main --------------------
if __name__ == "__main__":
    start_scheduler_once()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
