import re
import subprocess
from typing import Optional, List

# Используем snmpbulkwalk + стабильный вывод и «продолжение» при OID not increasing.
SNMP_WALK_CMD  = "snmpbulkwalk"
SNMP_WALK_OPTS = ["-v2c", "-On", "-OXs", "-Cc", "-Cr50"]  # числовые OID, компактные типы, continue, bulk
SNMP_SET_OPTS  = ["-v2c", "-On", "-OXs"]                  # set: формат не критичен, но пусть будет единый

def snmpwalk(host: str, community: str, oid: str, timeout=2) -> list[str]:
    cmd = [SNMP_WALK_CMD, *SNMP_WALK_OPTS, "-c", community, "-t", str(timeout), host, oid]
    out = subprocess.run(cmd, capture_output=True, text=True)
    # Даже если stderr содержит предупреждение, stdout нам важнее
    txt = (out.stdout or "").strip()
    return [ln.rstrip() for ln in txt.splitlines() if ln.strip()]

def snmpset(host: str, community: str, oid: str, typechar: str, value: str, timeout=2) -> bool:
    """
    Выполнить SNMP SET через net-snmp. typechar: i,u,t,a,o,x,d,b,s,…
    Пример: snmpset(ip, comm, f"{OID_IF_ADMIN_STATUS}.117", "i", "2")
    """
    cmd = ["snmpset", *SNMP_SET_OPTS, "-c", community, "-t", str(timeout), host, oid, typechar, value]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.returncode == 0

def parse_uptime_ticks(lines: List[str]) -> Optional[int]:
    if not lines:
        return None
    for ln in lines:
        # 1) Классика: Timeticks: (N)
        m = re.search(r"Timeticks:\s*\((\d+)\)", ln)
        if m:
            return int(m.group(1))
        # 2) Типизированный вывод: INTEGER:/Counter32:/Gauge32:
        m = re.search(r"=\s*(?:INTEGER|Counter32|Gauge32):\s*(\d+)", ln)
        if m:
            return int(m.group(1))
        # 3) «тихий» вывод: после "=" просто число
        m = re.search(r"=\s*(\d+)\s*$", ln)
        if m:
            return int(m.group(1))
        # 4) «совсем тихий» вывод (например с -Oqv): строка — одно число
        m = re.fullmatch(r"\s*(\d+)\s*", ln)
        if m:
            return int(m.group(1))
    return None

def get_sys_uptime_ticks(ip: str, community: str) -> Optional[int]:
    """Пробуем sysUpTime.0, затем hrSystemUptime.0; парсим максимально терпимо."""
    for oid in ("1.3.6.1.2.1.1.3.0",        # sysUpTime.0
                "1.3.6.1.2.1.25.1.1.0"):    # hrSystemUptime.0 (fallback)
        lines = snmpwalk(ip, community, oid) or []
        t = parse_uptime_ticks(lines)
        if t is not None:
            return t
    return None

def first_int(lines: list[str]) -> int | None:
    if not lines:
        return None
    s = "\n".join(lines)
    # INTEGER/Gauge32/Counter32/Timeticks (число)
    m = re.search(r"=\s*(?:INTEGER|Gauge32|Counter32|Timeticks):\s*\(?(-?\d+)\)?", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    # fallback: после "=" одно число
    m = re.search(r"=\s*(-?\d+)\s*$", s, re.MULTILINE)
    if m:
        return int(m.group(1))
    # ultra-quiet: строка — одно число
    for ln in lines:
        if ln.strip().lstrip("-").isdigit():
            return int(ln.strip())
    return None

def first_str(lines: list[str]) -> str | None:
    if not lines:
        return None
    s = "\n".join(lines)
    m = re.search(r"STRING:\s*\"([^\"]*)\"", s)
    if m:
        return m.group(1)
    m = re.search(r"=\s*(.*)$", s, re.MULTILINE)
    return m.group(1).strip() if m else None

# ---- DEVICE ----
OID_SYS_NAME        = "1.3.6.1.2.1.1.5.0"
OID_SYS_LOCATION    = "1.3.6.1.2.1.1.6.0"
OID_SYS_CONTACT     = "1.3.6.1.2.1.1.4.0"
OID_SYS_DESCR       = "1.3.6.1.2.1.1.1.0"
OID_SYS_UPTIME_TICK = "1.3.6.1.2.1.1.3.0"                 # Timeticks
OID_SYS_TIME_STR    = "1.3.6.1.4.1.3320.9.225.1.4.0"      # строка времени (BDCOM)

OID_CPU_USAGE       = "1.3.6.1.4.1.3320.9.109.1.1.1.1.0"  # %
OID_MEM_USAGE       = "1.3.6.1.4.1.3320.9.48.1.0"         # %
OID_TEMP_BOARD      = "1.3.6.1.4.1.3320.9.181.1.1.7.0"    # °C

# ---- IF/PORTS ----
OID_IFNAME          = "1.3.6.1.2.1.31.1.1.1.1"            # ifName.<ifIndex>
OID_IF_DESCR        = "1.3.6.1.2.1.2.2.1.2"               # ifDescr.<ifIndex>
OID_IF_ALIAS        = "1.3.6.1.2.1.31.1.1.1.18"           # ifAlias.<ifIndex>
OID_IF_OPER_STATUS  = "1.3.6.1.2.1.2.2.1.8"               # up(1)/down(2)
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"               # up(1)/down(2)/testing(3)

# 5-minute bitrate (BDCOM private)
OID_IF_IN_5M_BIT    = "1.3.6.1.4.1.3320.9.64.4.1.1.6"     # ifIn5MinBitRate.<ifIndex>
OID_IF_OUT_5M_BIT   = "1.3.6.1.4.1.3320.9.64.4.1.1.8"     # ifOut5MinBitRate.<ifIndex>

# ---- GPON per-ONU ----
OID_GPON_BIND_SN    = "1.3.6.1.4.1.3320.10.2.6.1.3"       # .<ifIndex>.<onuId> = STRING: "SN"
OID_GPON_STATUS     = "1.3.6.1.4.1.3320.10.3.3.1.4"       # .<globOnuIdx> => 0..3
OID_GPON_ONU_RX     = "1.3.6.1.4.1.3320.10.3.4.1.2"       # .<globOnuIdx> (×0.1 dBm)
OID_GPON_ONU_TX     = "1.3.6.1.4.1.3320.10.3.4.1.3"       # .<globOnuIdx> (×0.1 dBm)
OID_GPON_ONU_SN_TAB = "1.3.6.1.4.1.3320.10.3.1.1.4"       # .<globOnuIdx> = "SN"
OID_GPON_ONU_VENDOR = "1.3.6.1.4.1.3320.10.3.1.1.2"
OID_GPON_ONU_SW_A   = "1.3.6.1.4.1.3320.10.3.1.1.20"
OID_GPON_ONU_SW_B   = "1.3.6.1.4.1.3320.10.3.1.1.24"
OID_GPON_ONU_DIST   = "1.3.6.1.4.1.3320.10.3.1.1.33"
OID_GPON_ONU_LASTDN = "1.3.6.1.4.1.3320.10.3.1.1.35"

# ---- PON port optics ----
OID_PON_PORT_TX     = "1.3.6.1.4.1.3320.10.2.2.1.5"       # .<ifIndex> (×0.1 dBm)
OID_PON_PORT_RX     = "1.3.6.1.4.1.3320.10.2.3.1.3"       # .<ifIndex> (×0.1 dBm)

# ---- OLT reboot (всего устройства) ----
OID_OLT_REBOOT      = "1.3.6.1.4.1.3320.9.1847.0"         # обычно INTEGER 1

def parse_ifname(lines: list[str]) -> list[tuple[str,str]]:
    res=[]
    for ln in lines:
        m=re.search(r"\.(\d+)\s*=\s*STRING:\s*\"([^\"]+)\"", ln)
        if m: res.append((m.group(1), m.group(2)))
    return res

def parse_gpon_bind(lines):
    """
    Разбирает строки snmpwalk по веткам с привязками ONU:
    - .10.2.6.1.3 (bind из конфига) — может отдавать:
        * полные 16HEX SN: "4244434D8E53DDCB"
        * короткие "VEND:XXXXXXXX": "BDCM:B12A632B", "TPLG:0D382E18"
    - .10.3.1.1.4 (SN с ONU) — обычно всегда полные 16HEX.
    Возвращает список (ifindex, onuid, sn16hex).
    """
    out = []
    # шаблон индексов: ... .<ifIndex>.<onuId> = STRING: "<SN>"
    idx_re = re.compile(r"\.(\d+)\.(\d+)\s*=\s*STRING:\s*\"?([^\"]+)\"?$", re.IGNORECASE)

    def ascii4_to_hex(s4: str) -> str:
        # "BDCM" -> "4244434D"
        return "".join(f"{ord(c):02X}" for c in s4[:4])

    for ln in lines or []:
        m = idx_re.search(ln)
        if not m:
            continue
        ifi, onuid, raw = m.group(1), m.group(2), (m.group(3) or "").strip()

        sn_norm = None

        # 1) уже полный 16-символьный HEX?
        if re.fullmatch(r"[0-9A-Fa-f]{16}", raw):
            sn_norm = raw.upper()

        # 2) короткий формат "VEND:XXXXXXXX"?
        if sn_norm is None:
            m2 = re.fullmatch(r"([A-Za-z]{4})[:\-]([0-9A-Fa-f]{8})", raw)
            if m2:
                vend = m2.group(1).upper()          # BDCM / TPLG / GPON и т.п.
                tail = m2.group(2).upper()          # 8 hex
                sn_norm = ascii4_to_hex(vend) + tail  # 16 hex

        # 3) иногда встречаются пробелы или точки — почистим и проверим ещё раз
        if sn_norm is None:
            cleaned = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
            if len(cleaned) == 16:
                sn_norm = cleaned

        if sn_norm:
            out.append((ifi, onuid, sn_norm))

    return out

def find_glob_idx_by_sn(host: str, community: str, sn: str) -> str | None:
    lines = snmpwalk(host, community, OID_GPON_ONU_SN_TAB)
    pat = re.compile(r"\.(\d+)\s*=\s*STRING:\s*\"([0-9A-F]+)\"")
    for ln in lines:
        m = pat.search(ln)
        if m and m.group(2).upper() == sn:
            return m.group(1)
    return None

OFFLINE_REASON = {
    0: "none", 1: "dying-gasp", 2: "laser-always-on", 3: "admin-down",
    4: "omcc-down", 5: "unknown", 6: "pon-los", 7: "lcdg", 8: "wire-down",
    9: "omci-mismatch", 10: "password-mismatch", 11: "reboot", 12: "ranging-failed",
}
