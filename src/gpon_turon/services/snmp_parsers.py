import re


def _sn_from_raw(raw: str) -> str | None:
    sn = None
    if re.fullmatch(r"[0-9A-Fa-f]{16}", raw):
        sn = raw.upper()
    else:
        short = re.fullmatch(r"([A-Za-z]{4})[:\\-]([0-9A-Fa-f]{8})", raw)
        if short:
            vendor = short.group(1).upper()
            tail = short.group(2).upper()
            vendor_hex = "".join(f"{ord(ch):02X}" for ch in vendor[:4])
            sn = vendor_hex + tail

    if sn is None:
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
        if len(cleaned) >= 16:
            sn = cleaned[-16:]
    return sn


def parse_ifname(lines: list[str]) -> list[tuple[str, str]]:
    result = []
    for ln in lines or []:
        m = re.search(r"\.(\d+)\s*=\s*STRING:\s*\"([^\"]+)\"", ln)
        if m:
            result.append((m.group(1), m.group(2)))
    return result


def parse_gpon_bind(lines: list[str]) -> list[tuple[str, str, str]]:
    out = []
    idx_re = re.compile(r"\.(\d+)\.(\d+)\s*=\s*STRING:\s*\"?([^\"]+)\"?$", re.IGNORECASE)

    for ln in lines or []:
        m = idx_re.search(ln)
        if not m:
            continue
        ifindex, onu_id, raw = m.group(1), m.group(2), (m.group(3) or "").strip()

        sn = _sn_from_raw(raw)

        if sn:
            out.append((ifindex, onu_id, sn))
    return out


def parse_onu_sn_table(lines: list[str]) -> dict[str, str]:
    """
    Parse OID ...10.3.1.1.4 table:
    .<globIdx> = STRING: "SN"
    Returns: {glob_idx: sn_norm}
    """
    out: dict[str, str] = {}
    idx_re = re.compile(r"\.(\d+)\s*=\s*STRING:\s*\"?([^\"]+)\"?$", re.IGNORECASE)
    for ln in lines or []:
        m = idx_re.search(ln)
        if not m:
            continue
        idx = m.group(1)
        raw = (m.group(2) or "").strip()
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
        if len(cleaned) >= 16:
            out[idx] = cleaned[-16:]
    return out


def parse_onu_status_table(lines: list[str]) -> dict[str, int]:
    """
    Parse OID ...10.3.3.1.4 table:
    .<globIdx> = INTEGER: <status>
    Returns: {glob_idx: status_int}
    """
    out: dict[str, int] = {}
    idx_re = re.compile(r"\.(\d+)\s*=.*?(-?\d+)\s*$", re.IGNORECASE)
    for ln in lines or []:
        m = idx_re.search(ln)
        if not m:
            continue
        try:
            out[m.group(1)] = int(m.group(2))
        except ValueError:
            continue
    return out


def _extract_keyed_value_prefix(line: str) -> str | None:
    left = line.split("=", 1)[0].strip()
    if not left:
        return None
    if left.startswith("iso."):
        suffix = left[len("iso."):].strip(".")
        return f"1.{suffix}" if suffix else None
    if left.startswith("."):
        return left.strip(".")
    m = re.search(r"([0-9.]+)\s*$", left)
    if not m:
        return None
    return m.group(1).strip(".")


def _suffix_key(full_oid: str, base_oid: str) -> str | None:
    base = base_oid.strip().lstrip(".")
    full = (full_oid or "").strip().lstrip(".")
    prefix = f"{base}."
    if full.startswith(prefix):
        return full[len(prefix):]
    return None


def parse_tplink_keyed_strings(lines: list[str], base_oid: str | None = None) -> dict[str, str]:
    """
    Parse TP-Link keyed table lines:
    .<key_parts> = STRING: "value"
    Returns: {"1.2.1": "BDCM-8E53D54B", ...}
    """
    out: dict[str, str] = {}
    val_re = re.compile(r"=\s*STRING:\s*\"?([^\"]*)\"?$", re.IGNORECASE)
    for ln in lines or []:
        oid = _extract_keyed_value_prefix(ln)
        if not oid:
            continue
        key = _suffix_key(oid, base_oid) if base_oid else oid
        if not key:
            continue
        m = val_re.search(ln)
        if not m:
            continue
        out[key] = (m.group(1) or "").strip()
    return out


def parse_tplink_keyed_ints(lines: list[str], base_oid: str | None = None) -> dict[str, int]:
    """
    Parse TP-Link keyed integer table lines:
    .<key_parts> = INTEGER: value
    Returns: {"1.2.1": 3, ...}
    """
    out: dict[str, int] = {}
    val_re = re.compile(r"(-?\d+)\s*$", re.IGNORECASE)
    for ln in lines or []:
        oid = _extract_keyed_value_prefix(ln)
        if not oid:
            continue
        key = _suffix_key(oid, base_oid) if base_oid else oid
        if not key:
            continue
        m = val_re.search(ln)
        if not m:
            continue
        try:
            out[key] = int(m.group(1))
        except ValueError:
            continue
    return out


def parse_tplink_bind(
    sn_by_key: dict[str, str],
    port_name_by_key: dict[str, str],
    ifindex_by_port_name: dict[str, str],
) -> list[tuple[str, str, str]]:
    """
    Builds (ifindex, onu_id, sn_norm) for TP-Link tables where key looks like:
    <board>.<pon>.<onu>.
    """
    out: list[tuple[str, str, str]] = []
    for key, raw_sn in sn_by_key.items():
        parts = key.split(".")
        if len(parts) < 3:
            continue
        onu_id = parts[-1]
        if onu_id == "0":
            continue

        port_name = port_name_by_key.get(key, "").strip()
        if not port_name:
            continue
        ifindex = ifindex_by_port_name.get(port_name.lower())
        if not ifindex:
            continue

        sn = _sn_from_raw((raw_sn or "").strip())
        if not sn:
            continue
        out.append((ifindex, onu_id, sn))
    return out
