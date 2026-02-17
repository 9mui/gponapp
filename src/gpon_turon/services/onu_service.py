import re

from gpon_turon.repositories import OnuRepository
from gpon_turon.utils import norm_sn, vendor_from_sn
from gpon_turon.services.snmp_client import SnmpClient


class OnuService:
    OID_GPON_ONU_SN_TAB = "1.3.6.1.4.1.3320.10.3.1.1.4"
    OID_GPON_STATUS = "1.3.6.1.4.1.3320.10.3.3.1.4"
    OID_GPON_ONU_RX = "1.3.6.1.4.1.3320.10.3.4.1.2"
    OID_GPON_ONU_TX = "1.3.6.1.4.1.3320.10.3.4.1.3"
    OID_GPON_ONU_DIST = "1.3.6.1.4.1.3320.10.3.1.1.33"
    OID_GPON_ONU_LASTDN = "1.3.6.1.4.1.3320.10.3.1.1.35"
    OID_LAN_STATUS_4 = "1.3.6.1.4.1.3320.10.4.1.1.4"
    OID_LAN_STATUS_1 = "1.3.6.1.4.1.3320.10.4.1.1.1"
    OID_ONU_RESET = "1.3.6.1.4.1.3320.10.3.2.1.4"

    OFFLINE_REASON = {
        0: "none",
        1: "dying-gasp",
        2: "laser-always-on",
        3: "admin-down",
        4: "omcc-down",
        5: "unknown",
        6: "pon-los",
        7: "lcdg",
        8: "wire-down",
        9: "omci-mismatch",
        10: "password-mismatch",
        11: "reboot",
        12: "ranging-failed",
    }

    def __init__(self, repo: OnuRepository):
        self.repo = repo

    def find_by_sn(self, raw_sn: str):
        sn_norm = norm_sn(raw_sn)
        if len(sn_norm) != 16:
            return sn_norm, None, "-"
        row = self.repo.find_by_sn_norm(sn_norm)
        return sn_norm, row, vendor_from_sn(sn_norm)

    def get_live_metrics(self, row, sn_norm: str) -> dict[str, str]:
        if row is None or not row["olt_community"]:
            return {"status": "-", "lan_status": "-", "rx": "-", "tx": "-", "distance": "-", "last_down_reason": "-"}

        client = SnmpClient(timeout=1.0)
        glob_idx = self._find_glob_idx_by_sn(client, row["olt_ip"], row["olt_community"], sn_norm)
        if not glob_idx:
            return {"status": "-", "lan_status": "-", "rx": "-", "tx": "-", "distance": "-", "last_down_reason": "-"}

        status_raw = self._get_int(client, row["olt_ip"], row["olt_community"], f"{self.OID_GPON_STATUS}.{glob_idx}")
        rx_raw = self._get_int(client, row["olt_ip"], row["olt_community"], f"{self.OID_GPON_ONU_RX}.{glob_idx}")
        tx_raw = self._get_int(client, row["olt_ip"], row["olt_community"], f"{self.OID_GPON_ONU_TX}.{glob_idx}")
        dist_raw = self._get_int(client, row["olt_ip"], row["olt_community"], f"{self.OID_GPON_ONU_DIST}.{glob_idx}")
        lastdn_raw = self._get_int(client, row["olt_ip"], row["olt_community"], f"{self.OID_GPON_ONU_LASTDN}.{glob_idx}")
        lan_status = self._get_lan_status(client, row["olt_ip"], row["olt_community"], glob_idx)

        if status_raw == 3:
            status = "ONLINE"
        elif status_raw in (0, 1, 2):
            status = "OFFLINE"
        else:
            status = "-"

        # HGU profile: для TPLG (SN prefix 54504C47) считаем LAN UP, если ONU ONLINE.
        if status == "ONLINE" and sn_norm.startswith("54504C47"):
            lan_status = "UP"

        def dbm(x):
            return "-" if x is None else f"{x / 10:.1f}"

        def meters(x):
            return "-" if x is None else f"{x / 10:.1f} м"

        return {
            "status": status,
            "lan_status": lan_status,
            "rx": dbm(rx_raw),
            "tx": dbm(tx_raw),
            "distance": meters(dist_raw),
            "last_down_reason": self.OFFLINE_REASON.get(lastdn_raw, str(lastdn_raw) if lastdn_raw is not None else "-"),
        }

    def reboot_onu(self, row, sn_norm: str) -> tuple[bool, str]:
        if row is None or not row["olt_community"]:
            return False, "ONU или community не найдены"

        client = SnmpClient(timeout=1.0)
        glob_idx = self._find_glob_idx_by_sn(client, row["olt_ip"], row["olt_community"], sn_norm)
        if not glob_idx:
            return False, "Не удалось определить ONU на OLT"

        ok = client.set(
            host=row["olt_ip"],
            community=row["olt_community"],
            oid=f"{self.OID_ONU_RESET}.{glob_idx}",
            typechar="i",
            value="1",
        )
        if ok:
            return True, f"Команда перезагрузки отправлена ({row['olt_ip']}, index {glob_idx})"
        return False, "Не удалось отправить команду перезагрузки ONU"

    def _find_glob_idx_by_sn(self, client: SnmpClient, ip: str, community: str, sn_norm: str) -> str | None:
        lines = client.walk(ip, community, self.OID_GPON_ONU_SN_TAB)
        pattern = re.compile(r"\.(\d+)\s*=\s*STRING:\s*\"([^\"]+)\"")
        for ln in lines:
            m = pattern.search(ln)
            if not m:
                continue
            idx, raw = m.group(1), m.group(2)
            if norm_sn(raw) == sn_norm:
                return idx
        return None

    def _get_int(self, client: SnmpClient, ip: str, community: str, oid: str) -> int | None:
        lines = client.walk(ip, community, oid)
        for ln in lines:
            m = re.search(r"(-?\d+)\s*$", ln)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
        return None

    def _get_lan_status(self, client: SnmpClient, ip: str, community: str, glob_idx: str) -> str:
        vals: list[int] = []
        for uni in range(1, 5):
            for oid in (
                f"{self.OID_LAN_STATUS_4}.{glob_idx}.{uni}",
                f"{self.OID_LAN_STATUS_4}.{uni}.{glob_idx}",
                f"{self.OID_LAN_STATUS_1}.{glob_idx}.{uni}",
                f"{self.OID_LAN_STATUS_1}.{uni}.{glob_idx}",
            ):
                v = self._get_int(client, ip, community, oid)
                if v is not None:
                    vals.append(v)
                    break
        if any(v == 1 for v in vals):
            return "UP"
        if any(v == 2 for v in vals):
            return "DOWN"
        return "-"
