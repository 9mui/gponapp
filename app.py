import re, csv, io, time, sqlite3
from flask import Flask, render_template, request, redirect, url_for, abort, Response, flash, g
from models import ensure_db, DB
from snmp import (
    snmpwalk, snmpset, first_int, first_str,
    OID_IFNAME, OID_IF_DESCR, OID_IF_ALIAS, OID_IF_OPER_STATUS, OID_IF_ADMIN_STATUS,
    OID_IF_IN_5M_BIT, OID_IF_OUT_5M_BIT,
    OID_GPON_BIND_SN, OID_GPON_STATUS, OID_GPON_ONU_RX, OID_GPON_ONU_TX,
    OID_GPON_ONU_VENDOR, OID_GPON_ONU_SW_A, OID_GPON_ONU_SW_B, OID_GPON_ONU_DIST, OID_GPON_ONU_LASTDN,
    OID_GPON_ONU_SN_TAB, OID_PON_PORT_TX, OID_PON_PORT_RX,
    OID_SYS_NAME, OID_SYS_LOCATION, OID_SYS_CONTACT, OID_SYS_DESCR, OID_SYS_UPTIME_TICK, OID_SYS_TIME_STR,
    OID_CPU_USAGE, OID_MEM_USAGE, OID_TEMP_BOARD, OID_OLT_REBOOT,
    parse_ifname, parse_gpon_bind, find_glob_idx_by_sn, OFFLINE_REASON
)

app = Flask(__name__)
app.secret_key = "gponapp-secret"
ensure_db()


def get_db():
    if "db" not in g:
        ensure_db()
        g.db = sqlite3.connect(DB)
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# --- доп. константа: reset ONU (admin reset) ---
OID_ONU_RESET = "1.3.6.1.4.1.3320.10.3.2.1.4"

# --- утилиты ---
def norm_sn(s: str) -> str:
    return re.sub(r"[^0-9A-F]", "", (s or "").upper())

def ticks_to_hms(ticks: int | None) -> str:
    if ticks is None: return "-"
    sec = ticks // 100
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{d}d {h:02}:{m:02}:{s:02}"

def get_sys_uptime_ticks(ip: str, community: str) -> int | None:
    """
    Возвращает sysUpTime в тиках (1 тик = 1/100 сек).
    Пробуем стандартный sysUpTime.0 и запасной hrSystemUptime.0.
    Парсим ТОЛЬКО число в скобках после 'Timeticks:'.
    """
    for oid in ("1.3.6.1.2.1.1.3.0",      # sysUpTime.0
                "1.3.6.1.2.1.25.1.1.0"):  # hrSystemUptime.0 (fallback)
        lines = snmpwalk(ip, community, oid) or []
        for ln in lines:
            m = re.search(r"Timeticks:\s*\((\d+)\)", ln)
            if m:
                return int(m.group(1))
    return None

def get_cpu_percent(ip: str, community: str) -> int | None:
    lines = snmpwalk(ip, community, "1.3.6.1.2.1.25.3.3.1.2")  # hrProcessorLoad
    vals = []
    for ln in lines or []:
        m = re.search(r"(?:INTEGER|Gauge32):\s*(\d+)", ln)
        if m:
            vals.append(int(m.group(1)))
    if vals:
        return round(sum(vals) / len(vals))
    return None

def meters_from_dm(raw):
    """BDCOM distance в decimeters (0.1 m) -> meters (float, 1 decimal)."""
    try:
        return None if raw is None else round(float(raw) / 10.0, 1)
    except Exception:
        return None

def refresh_olt_cache(ip: str) -> bool:
    """
    Переопрашивает один OLT и обновляет кэш ponports/gpon в БД.
    Возвращает True, если получилось.
    """
    conn = get_db()
    got = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()
    if not got:
        return False
    community = got[0]

    # ifName -> ponports
    ifs = parse_ifname(snmpwalk(ip, community, OID_IFNAME))
    conn.execute("DELETE FROM ponports WHERE olt_ip = ?", (ip,))
    for ifi, name in ifs:
        conn.execute(
            "INSERT INTO ponports(olt_ip, ifindex, name) VALUES(?,?,?)",
            (ip, ifi, name)
        )

    # GPON привязки (порт, onu-id, SN) -> gpon
    binds = parse_gpon_bind(snmpwalk(ip, community, OID_GPON_BIND_SN))
    conn.execute("DELETE FROM gpon WHERE olt_ip = ?", (ip,))
    for ifi, onuid, sn in binds:
        conn.execute(
            "INSERT OR REPLACE INTO gpon(olt_ip, portonu, idonu, snonu) VALUES(?,?,?,?)",
            (ip, ifi, onuid, sn)
        )
    conn.commit()
    return True

# ---------- Главная / поиск ----------

@app.get("/")
def home():
    conn = get_db()
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
    if sn:
        return redirect(url_for("onu_by_sn", sn=sn))
    flash("Введите корректный SN (16 HEX символов)", "info")
    return redirect(request.referrer or url_for("home"))

# список/добавление OLT
@app.get("/olts")
def list_olts():
    conn = get_db()
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
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO olts(hostname,ip,community,vendor) VALUES(?,?,?,?)",
                 (hostname, ip, comm, vendor))
    conn.commit()
    flash(f"OLT {hostname} ({ip}) добавлен", "info")
    return redirect(url_for("home"))

# ---------- OLT pages ----------

@app.get("/olt/<ip>")
def olt(ip):
    conn = get_db()
    row = conn.execute("SELECT hostname, community, vendor FROM olts WHERE ip = ?", (ip,)).fetchone()
    if not row: abort(404)
    hostname, community, vendor = row
    ports = conn.execute(
        """
        SELECT p.ifindex, p.name,
               COALESCE((SELECT COUNT(*) FROM gpon g WHERE g.olt_ip=p.olt_ip AND g.portonu=p.ifindex),0) AS cnt
        FROM ponports p
        WHERE p.olt_ip = ?
          AND p.name LIKE 'GPON%/%'
          AND p.name NOT LIKE '%:%'
        ORDER BY CAST(p.ifindex AS INT)
        """, (ip,)
    ).fetchall()
    return render_template("olt_ports.html", ip=ip, hostname=hostname, ports=ports)

@app.post("/olt/<ip>/refresh")
def olt_refresh(ip):
    refresh_olt_cache(ip)
    flash("Кэш портов и привязок обновлён", "info")
    return redirect(url_for("olt", ip=ip))

@app.get("/olt/<ip>/device")
def olt_device(ip):
    conn = get_db()
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

# ---------- Uplinks (LAN) ----------

@app.get("/olt/<ip>/uplinks")
def olt_uplinks(ip):
    conn = get_db()
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
        if name.startswith("GPON"):  # аплинки ≠ gpon
            continue
        in5  = first_int(snmpwalk(ip, community, f"{OID_IF_IN_5M_BIT}.{ifi}"))
        out5 = first_int(snmpwalk(ip, community, f"{OID_IF_OUT_5M_BIT}.{ifi}"))
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

# ---------- Port page (PON) ----------

@app.get("/olt/<ip>/port/<ifindex>")
def port(ip, ifindex):
    conn = get_db()
    row = conn.execute("SELECT name FROM ponports WHERE olt_ip = ? AND ifindex = ?", (ip, ifindex)).fetchone()
    if not row: abort(404)
    port_name = row[0]
    onus = conn.execute(
        "SELECT snonu, idonu FROM gpon WHERE olt_ip = ? AND portonu = ? ORDER BY CAST(idonu AS INT)",
        (ip, ifindex)
    ).fetchall()
    comm = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()[0]
    tx = first_int(snmpwalk(ip, comm, f"{OID_PON_PORT_TX}.{ifindex}"))
    rx = first_int(snmpwalk(ip, comm, f"{OID_PON_PORT_RX}.{ifindex}"))
    stat = first_int(snmpwalk(ip, comm, f"{OID_IF_OPER_STATUS}.{ifindex}"))
    def dbm(x): return None if x is None else x/10.0
    return render_template("port_onus.html",
                           ip=ip, port_name=port_name, ifindex=ifindex, onus=onus,
                           pon_tx=dbm(tx), pon_rx=dbm(rx), port_status=stat)

# экспорт ONU на порту в CSV
@app.get("/olt/<ip>/port/<ifindex>/export.csv")
def port_export_csv(ip, ifindex):
    conn = get_db()
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

# bounce PON-порта (ifAdmin down->up)
@app.post("/olt/<ip>/port/<ifindex>/bounce")
def port_bounce(ip, ifindex):
    conn = get_db()
    row = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()
    if not row: abort(404)
    community = row[0]
    ok1 = snmpset(ip, community, f"{OID_IF_ADMIN_STATUS}.{ifindex}", "i", "2")
    time.sleep(1)
    ok2 = snmpset(ip, community, f"{OID_IF_ADMIN_STATUS}.{ifindex}", "i", "1")
    flash("Порт отправлен в down→up" if (ok1 and ok2) else "Не удалось изменить ifAdminStatus", "info")
    return redirect(url_for("port", ip=ip, ifindex=ifindex))

# reboot всего OLT
@app.post("/olt/<ip>/reboot")
def olt_reboot(ip):
    conn = get_db()
    row = conn.execute("SELECT community FROM olts WHERE ip = ?", (ip,)).fetchone()
    if not row: abort(404)
    community = row[0]
    ok = snmpset(ip, community, OID_OLT_REBOOT, "i", "1")
    flash("Команда перезагрузки отправлена" if ok else "Не удалось выполнить перезагрузку", "info")
    return redirect(url_for("olt", ip=ip))

@app.post("/olts/<ip>/delete")
def delete_olt(ip):
    conn = get_db()
    conn.execute("DELETE FROM gpon WHERE olt_ip = ?", (ip,))
    conn.execute("DELETE FROM ponports WHERE olt_ip = ?", (ip,))
    conn.execute("DELETE FROM olts WHERE ip = ?", (ip,))
    conn.commit()
    flash(f"OLT {ip} удалён", "info")
    return redirect(url_for("home"))

# ---------- ONU page ----------
@app.get("/onu/sn/<sn>")
def onu_by_sn(sn):
    sn = norm_sn(sn)

    # 1) первая попытка: ищем в кэше
    conn = get_db()
    row = conn.execute("""
        SELECT olt_ip, portonu, idonu
        FROM gpon
        WHERE REPLACE(UPPER(snonu),' ','') = ?
        LIMIT 1
    """, (sn,)).fetchone()

    # 1b) если не нашли — автоопрос всех OLT и повторный поиск
    if not row:
        conn = get_db()
        olts = [r[0] for r in conn.execute("SELECT ip FROM olts").fetchall()]
        for ip_ in olts:
            try: refresh_olt_cache(ip_)
            except Exception: pass
        conn = get_db()
        row = conn.execute("""
            SELECT olt_ip, portonu, idonu
            FROM gpon
            WHERE REPLACE(UPPER(snonu),' ','') = ?
            LIMIT 1
        """, (sn,)).fetchone()

    if not row:
        return render_template("not_found.html", q=sn)

    olt_ip, port_if, onuid_port = row
    conn = get_db()
    community = conn.execute("SELECT community FROM olts WHERE ip = ?", (olt_ip,)).fetchone()[0]

    # имя порта и UNI
    conn = get_db()
    row_name = conn.execute("SELECT name FROM ponports WHERE olt_ip = ? AND ifindex = ?", (olt_ip, port_if)).fetchone()
    port_name_base = row_name[0] if row_name else f"ifIndex {port_if}"
    uni = conn.execute("SELECT ifindex, name FROM ponports WHERE olt_ip = ? AND name = ?",
                       (olt_ip, f"{port_name_base}:{onuid_port}")).fetchone()
    if uni:
        uni_ifindex, port_name_full = uni[0], uni[1]
    else:
        uni_ifindex, port_name_full = None, f"{port_name_base}:{onuid_port}"

    # глобальный индекс
    glob_idx = find_glob_idx_by_sn(olt_ip, community, sn)

    # универсальные геттеры (разные типы индексов)
    def first_non_none_int(base_oid: str):
        for idx in [glob_idx, uni_ifindex, f"{port_if}.{onuid_port}"]:
            if not idx:
                continue
            val = first_int(snmpwalk(olt_ip, community, f"{base_oid}.{idx}"))
            if val is not None:
                return val
        return None

    def first_non_none_str(base_oid: str):
        for idx in [glob_idx, uni_ifindex, f"{port_if}.{onuid_port}"]:
            if not idx:
                continue
            val = first_str(snmpwalk(olt_ip, community, f"{base_oid}.{idx}"))
            if val and "No Such" not in val:
                return val
        return None

    # метрики и доп.инфо
    status   = first_non_none_int(OID_GPON_STATUS)        # 0..3
    rx_raw   = first_non_none_int(OID_GPON_ONU_RX)        # ×0.1 dBm
    tx_raw   = first_non_none_int(OID_GPON_ONU_TX)        # ×0.1 dBm
    vendor   = first_non_none_str(OID_GPON_ONU_VENDOR)
    dist_dm  = first_non_none_int(OID_GPON_ONU_DIST)      # дециметры
    lastdn   = first_non_none_int(OID_GPON_ONU_LASTDN)

    # --- НОВОЕ: статусы LAN (UNI) портов ONU ---
    # OID Per ONU LAN Status: 1.3.6.1.4.1.3320.10.4.1.1.4
    # Пробуем индексацию <globIdx>.<uni> и <uni>.<globIdx> для портов 1..4
    def onu_lan_statuses(max_uni=4):
        results = []
        for uni_port in range(1, max_uni + 1):
            val = None
            if glob_idx:
                # вариант A: .<globIdx>.<uni>
                val = first_int(snmpwalk(olt_ip, community, f"1.3.6.1.4.1.3320.10.4.1.1.4.{glob_idx}.{uni_port}"))
                if val is None:
                    # вариант B: .<uni>.<globIdx>
                    val = first_int(snmpwalk(olt_ip, community, f"1.3.6.1.4.1.3320.10.4.1.1.4.{uni_port}.{glob_idx}"))
            # нормализация
            if val == 1:
                txt = "up"
            elif val == 2:
                txt = "down"
            elif val is None:
                txt = "-"
            else:
                txt = str(val)
            results.append({"uni": uni_port, "val": val, "text": txt})
        return results

    lan_list = onu_lan_statuses()

    # приведение величин/подписей
    def dbm(x): return None if x is None else x/10.0
    distance = meters_from_dm(dist_dm)
    lastdn_txt = OFFLINE_REASON.get(lastdn, str(lastdn) if lastdn is not None else "-")

    return render_template("onu.html",
        sn=sn, olt_ip=olt_ip, port_if=port_if, onuid=onuid_port,
        port_name_full=port_name_full, uni_ifindex=uni_ifindex,
        status=status, rx=dbm(rx_raw), tx=dbm(tx_raw),
        vendor=vendor, distance=distance, last_down=lastdn_txt,
        lan_list=lan_list  # <- добавили в шаблон
    )

# перезагрузка ONU по SN
@app.post("/onu/reboot/<sn>")
def onu_reboot(sn):
    sn_norm = norm_sn(sn)
    conn = get_db()
    row = conn.execute("""
        SELECT olt_ip FROM gpon
        WHERE REPLACE(UPPER(snonu),' ','') = ?
        LIMIT 1
    """, (sn_norm,)).fetchone()
    if not row:
        flash(f"ONU {sn_norm} не найдена в БД", "error")
        return redirect(url_for("home"))
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
