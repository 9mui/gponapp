import re
from typing import Optional, List, Iterable, Dict

from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
    ObjectType, ObjectIdentity, bulkCmd, setCmd
)
from pysnmp.proto.rfc1902 import (
    Integer, OctetString, TimeTicks, Counter32, Gauge32,
    IpAddress, Unsigned32, ObjectIdentifier
)


def _fmt_value(val) -> str:
    """Convert pysnmp value to net-snmp-like text representation."""
    if isinstance(val, OctetString):
        return f'STRING: "{val.prettyPrint()}"'
    if isinstance(val, TimeTicks):
        return f'Timeticks: ({int(val)})'
    if isinstance(val, Counter32):
        return f'Counter32: {int(val)}'
    if isinstance(val, Gauge32):
        return f'Gauge32: {int(val)}'
    if isinstance(val, Unsigned32):
        return f'Unsigned32: {int(val)}'
    if isinstance(val, IpAddress):
        return f'IpAddress: {val.prettyPrint()}'
    if isinstance(val, Integer):
        return f'INTEGER: {int(val)}'
    if isinstance(val, ObjectIdentifier):
        return f'OID: {val.prettyPrint()}'
    return val.prettyPrint()


def snmpwalk(host: str, community: str, oid: str, timeout=2) -> list[str]:
    """Walk single OID subtree using pysnmp without spawning processes."""
    engine = SnmpEngine()
    target = UdpTransportTarget((host, 161), timeout=timeout)
    obj = ObjectType(ObjectIdentity(oid))
    res: List[str] = []
    for (errInd, errStat, errIdx, varBinds) in bulkCmd(
        engine,
        CommunityData(community, mpModel=1),
        target,
        ContextData(),
        0, 25,
        obj,
        lexicographicMode=False,
    ):
        if errInd or errStat:
            break
        for vb in varBinds:
            res.append(f"{vb[0].prettyPrint()} = {_fmt_value(vb[1])}")
    return res


def snmpwalk_bulk(host: str, community: str, oids: Iterable[str], timeout=2) -> Dict[str, List[str]]:
    """Fetch several OID trees in a single bulk request."""
    engine = SnmpEngine()
    target = UdpTransportTarget((host, 161), timeout=timeout)
    obj_types = [ObjectType(ObjectIdentity(o)) for o in oids]
    res: Dict[str, List[str]] = {o: [] for o in oids}
    for (errInd, errStat, errIdx, varBinds) in bulkCmd(
        engine,
        CommunityData(community, mpModel=1),
        target,
        ContextData(),
        0, 25,
        *obj_types,
        lexicographicMode=False,
    ):
        if errInd or errStat:
            break
        for vb in varBinds:
            oid_str = vb[0].prettyPrint()
            line = f"{oid_str} = {_fmt_value(vb[1])}"
            for base in res:
                if oid_str.startswith(base):
                    res[base].append(line)
                    break
    return res


def snmpset(host: str, community: str, oid: str, typechar: str, value: str, timeout=2) -> bool:
    """Perform SNMP SET using pysnmp. typechar: i,u,t,a,o,s,…"""
    typechar = (typechar or '').lower()
    tmap = {
        'i': Integer,
        'u': Unsigned32,
        't': TimeTicks,
        'a': IpAddress,
        'o': ObjectIdentifier,
        's': OctetString,
        'x': OctetString,
        'd': OctetString,
        'b': OctetString,
    }
    cls = tmap.get(typechar, OctetString)
    try:
        if cls in (Integer, Unsigned32, TimeTicks):
            val_obj = cls(int(value))
        elif cls is IpAddress:
            val_obj = cls(value)
        elif cls is ObjectIdentifier:
            val_obj = cls(value)
        else:
            val_obj = cls(value)
    except Exception:
        return False
    errorIndication, errorStatus, errorIndex, varBinds = next(
        setCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((host, 161), timeout=timeout),
            ContextData(),
            ObjectType(ObjectIdentity(oid), val_obj)
        )
    )
    return not errorIndication and errorStatus == 0

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
