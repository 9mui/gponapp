import re


HEX16_RE = re.compile(r"[0-9A-F]{16}")


def norm_sn(value: str) -> str:
    sn = (value or "").strip().upper()
    short = re.fullmatch(r"([A-Z]{4})[:\-]([0-9A-F]{8})", sn)
    if short:
        vendor = short.group(1)
        tail = short.group(2)
        vendor_hex = "".join(f"{ord(ch):02X}" for ch in vendor)
        return vendor_hex + tail
    if ":" in sn and len(sn.split(":", 1)[1]) >= 16:
        sn = sn.split(":", 1)[1]
    sn = re.sub(r"[^0-9A-F]", "", sn)
    match = HEX16_RE.search(sn)
    if match:
        return match.group(0)
    return sn[-16:] if len(sn) >= 16 else sn


def vendor_from_sn(sn_value: str) -> str:
    sn_norm = norm_sn(sn_value)
    if len(sn_norm) < 8:
        return "-"
    prefix = sn_norm[:8]
    try:
        vendor = bytes.fromhex(prefix).decode("ascii")
    except Exception:
        return "-"
    if all(32 <= ord(ch) <= 126 for ch in vendor):
        return vendor
    return "-"
