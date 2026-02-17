import subprocess


class SnmpClient:
    def __init__(self, timeout: float = 1.0):
        self.timeout = timeout

    def walk(self, host: str, community: str, oid: str) -> list[str]:
        lines, _ = self.walk_with_status(host=host, community=community, oid=oid)
        return lines

    def walk_with_status(self, host: str, community: str, oid: str) -> tuple[list[str], bool]:
        cmd = [
            "snmpbulkwalk",
            "-v2c",
            "-On",
            "-OXs",
            "-Cc",
            "-Cr50",
            "-c",
            community,
            "-t",
            str(self.timeout),
            host,
            oid,
        ]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            return [], False
        txt = (out.stdout or "").strip()
        return [ln.rstrip() for ln in txt.splitlines() if ln.strip()], True

    def set(self, host: str, community: str, oid: str, typechar: str, value: str) -> bool:
        cmd = [
            "snmpset",
            "-v2c",
            "-On",
            "-OXs",
            "-c",
            community,
            "-t",
            str(self.timeout),
            host,
            oid,
            typechar,
            value,
        ]
        out = subprocess.run(cmd, capture_output=True, text=True)
        return out.returncode == 0
