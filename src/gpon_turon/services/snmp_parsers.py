import re


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
