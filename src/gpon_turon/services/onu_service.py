import re

from gpon_turon.repositories import OnuRepository
from gpon_turon.services.olt_service import OltService
from gpon_turon.services.snmp_parsers import parse_tplink_keyed_ints, parse_tplink_keyed_strings
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
    OID_TPLINK_ONU_SN = "1.3.6.1.4.1.11863.6.100.1.7.2.1.6"
    OID_TPLINK_ONLINE_STATUS = "1.3.6.1.4.1.11863.6.100.1.6.2.1.21"
    OID_TPLINK_ONU_STATUS = "1.3.6.1.4.1.11863.6.100.1.7.2.1.7"
    OID_TPLINK_ONU_ACTIVE = "1.3.6.1.4.1.11863.6.100.1.7.2.1.14"
    OID_TPLINK_ONU_VENDOR = "1.3.6.1.4.1.11863.6.100.1.7.2.1.15"
    OID_TPLINK_ONU_RX = "1.3.6.1.4.1.11863.6.100.1.7.2.1.26"
    OID_TPLINK_ONU_TX = "1.3.6.1.4.1.11863.6.100.1.7.2.1.27"
    OID_TPLINK_ONU_DISTANCE = "1.3.6.1.4.1.11863.6.100.1.7.2.1.18"
    OID_TPLINK_ONU_LASTDN = "1.3.6.1.4.1.11863.6.100.1.7.2.1.42"
    OID_TPLINK_ONU_REBOOT = "1.3.6.1.4.1.11863.6.100.1.7.2.1.41"
    OID_TPLINK_LAN_LINK = "1.3.6.1.4.1.11863.6.100.1.7.2.1.38"

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

    def get_olt_online_status(self, row) -> bool | None:
        if row is None or not row["olt_ip"] or not row["olt_community"]:
            return None
        return OltService.get_poll_status(str(row["olt_ip"]))

    def get_live_metrics(self, row, sn_norm: str) -> dict[str, str]:
        if row is None or not row["olt_community"]:
            return {"status": "-", "lan_status": "-", "rx": "-", "tx": "-", "distance": "-", "last_down_reason": "-"}

        vendor = (row["olt_vendor"] or "").strip().lower()
        if vendor == "tplink":
            return self._get_live_metrics_tplink(row, sn_norm)

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
        vendor = (row["olt_vendor"] or "").strip().lower()
        if vendor == "tplink":
            client = SnmpClient(timeout=1.0)
            keys = self._find_tplink_keys_by_sn(client, row["olt_ip"], row["olt_community"], sn_norm)
            if not keys:
                return False, "Не удалось определить ONU на OLT"
            key = self._choose_tplink_best_key(client, row["olt_ip"], row["olt_community"], keys)
            ok = client.set(
                host=row["olt_ip"],
                community=row["olt_community"],
                oid=f"{self.OID_TPLINK_ONU_REBOOT}.{key}",
                typechar="i",
                value="2",
            )
            if ok:
                return True, f"Команда перезагрузки отправлена ({row['olt_ip']}, key {key})"
            return False, "Не удалось отправить команду перезагрузки ONU"

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

    def _get_live_metrics_tplink(self, row, sn_norm: str) -> dict[str, str]:
        client = SnmpClient(timeout=1.0)
        keys = self._find_tplink_keys_by_sn(client, row["olt_ip"], row["olt_community"], sn_norm)
        if not keys:
            return {"status": "-", "lan_status": "-", "rx": "-", "tx": "-", "distance": "-", "last_down_reason": "-"}

        online_by_key = self._tplink_walk_keyed_ints(client, row["olt_ip"], row["olt_community"], self.OID_TPLINK_ONLINE_STATUS)
        status_by_key = self._tplink_walk_keyed_ints(client, row["olt_ip"], row["olt_community"], self.OID_TPLINK_ONU_STATUS)
        rx_by_key = self._tplink_walk_keyed_strings(client, row["olt_ip"], row["olt_community"], self.OID_TPLINK_ONU_RX)
        tx_by_key = self._tplink_walk_keyed_strings(client, row["olt_ip"], row["olt_community"], self.OID_TPLINK_ONU_TX)
        dist_by_key = self._tplink_walk_keyed_ints(client, row["olt_ip"], row["olt_community"], self.OID_TPLINK_ONU_DISTANCE)
        lastdn_by_key = self._tplink_walk_keyed_strings(client, row["olt_ip"], row["olt_community"], self.OID_TPLINK_ONU_LASTDN)

        samples: list[dict[str, object]] = []
        for key in keys:
            online_raw = online_by_key.get(key)
            status_raw = status_by_key.get(key)
            rx_raw = rx_by_key.get(key)
            tx_raw = tx_by_key.get(key)
            dist_raw = dist_by_key.get(key)
            lastdn_raw = lastdn_by_key.get(key)
            samples.append(
                {
                    "key": key,
                    "online_raw": online_raw,
                    "status_raw": status_raw,
                    "rx_raw": rx_raw,
                    "tx_raw": tx_raw,
                    "dist_raw": dist_raw,
                    "lastdn_raw": lastdn_raw,
                }
            )

        def _score(sample: dict[str, object]) -> tuple[int, int]:
            online_raw = sample["online_raw"]
            has_optics = int(bool(sample["rx_raw"] or sample["tx_raw"]))
            online = int(online_raw == 1)
            return (online, has_optics)

        chosen = max(samples, key=_score)
        online_raw = chosen["online_raw"]
        status_raw = chosen["status_raw"]
        rx_raw = chosen["rx_raw"]
        tx_raw = chosen["tx_raw"]
        dist_raw = chosen["dist_raw"]
        lastdn_raw = chosen["lastdn_raw"]

        if status_raw == 3:
            status = "ONLINE"
        elif online_raw == 1:
            status = "ONLINE"
        elif online_raw == 0:
            status = "OFFLINE"
        elif status_raw == 4 and (rx_raw or tx_raw):
            status = "ONLINE"
        elif status_raw in (0, 1, 2, 4):
            status = "OFFLINE"
        else:
            status = "-"

        distance = "-" if dist_raw is None else f"{dist_raw:.1f} м"
        return {
            "status": status,
            "lan_status": "NOT_SUPPORTED",
            "rx": rx_raw or "-",
            "tx": tx_raw or "-",
            "distance": distance,
            "last_down_reason": lastdn_raw or "-",
        }

    def _choose_tplink_best_key(self, client: SnmpClient, ip: str, community: str, keys: list[str]) -> str:
        online_by_key = self._tplink_walk_keyed_ints(client, ip, community, self.OID_TPLINK_ONLINE_STATUS)
        rx_by_key = self._tplink_walk_keyed_strings(client, ip, community, self.OID_TPLINK_ONU_RX)

        best_key = keys[0]
        best_score = (-1, -1)
        for key in keys:
            online_raw = online_by_key.get(key)
            rx_raw = rx_by_key.get(key)
            score = (int(online_raw == 1), int(bool(rx_raw)))
            if score > best_score:
                best_score = score
                best_key = key
        return best_key

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

    def _find_tplink_key_by_sn(self, client: SnmpClient, ip: str, community: str, sn_norm: str) -> str | None:
        keys = self._find_tplink_keys_by_sn(client, ip, community, sn_norm)
        return keys[0] if keys else None

    def _find_tplink_keys_by_sn(self, client: SnmpClient, ip: str, community: str, sn_norm: str) -> list[str]:
        lines = client.walk(ip, community, self.OID_TPLINK_ONU_SN)
        pattern = re.compile(r"\.([0-9.]+)\s*=\s*STRING:\s*\"?([^\"]+)\"?")
        base = self.OID_TPLINK_ONU_SN.lstrip(".")
        prefix = f"{base}."
        keys: list[str] = []
        for ln in lines:
            m = pattern.search(ln)
            if not m:
                continue
            full_oid, raw = m.group(1), m.group(2)
            if ln.strip().startswith("iso."):
                full_oid = f"1.{full_oid}"
            full_oid = full_oid.lstrip(".")
            if not full_oid.startswith(prefix):
                continue
            key = full_oid[len(prefix):]
            if norm_sn(raw) == sn_norm:
                keys.append(key)
        return keys

    def _tplink_get_int_by_key(
        self,
        client: SnmpClient,
        ip: str,
        community: str,
        base_oid: str,
        key: str,
    ) -> int | None:
        lines = client.walk(ip, community, f"{base_oid}.{key}")
        if not lines:
            return None
        m = re.search(r"(-?\d+)\s*$", lines[0])
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    def _tplink_get_str_by_key(
        self,
        client: SnmpClient,
        ip: str,
        community: str,
        base_oid: str,
        key: str,
    ) -> str | None:
        lines = client.walk(ip, community, f"{base_oid}.{key}")
        if not lines:
            return None
        m = re.search(r"STRING:\s*\"([^\"]*)\"", lines[0])
        if m:
            value = (m.group(1) or "").strip()
        elif "=" in lines[0]:
            value = lines[0].split("=", 1)[1].strip().strip('"')
        else:
            return None
        if value in {"", "--"}:
            return None
        return value

    def _tplink_walk_keyed_ints(
        self,
        client: SnmpClient,
        ip: str,
        community: str,
        base_oid: str,
    ) -> dict[str, int]:
        lines = client.walk(ip, community, base_oid)
        return parse_tplink_keyed_ints(lines, base_oid)

    def _tplink_walk_keyed_strings(
        self,
        client: SnmpClient,
        ip: str,
        community: str,
        base_oid: str,
    ) -> dict[str, str]:
        lines = client.walk(ip, community, base_oid)
        parsed = parse_tplink_keyed_strings(lines, base_oid)
        return {k: v for k, v in parsed.items() if v not in {"", "--"}}
