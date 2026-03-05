import threading
import time
import logging
import re

from gpon_turon.repositories import OltRepository
from gpon_turon.services.snmp_client import SnmpClient
from gpon_turon.services.snmp_parsers import (
    parse_gpon_bind,
    parse_ifname,
    parse_onu_sn_table,
    parse_onu_status_table,
    parse_tplink_keyed_ints,
    parse_tplink_keyed_strings,
)
from gpon_turon.utils import norm_sn

logger = logging.getLogger(__name__)


class OltService:
    def __init__(self, repo: OltRepository):
        self.repo = repo

    def list_olts(self):
        return self.repo.list_all()

    def add_olt(self, hostname: str, ip: str, community: str, vendor: str) -> None:
        self.repo.create(
            hostname=hostname.strip(),
            ip=ip.strip(),
            community=community.strip() or "private",
            vendor=(vendor.strip() or "bdcom").lower(),
        )

    def delete_olt(self, olt_id: int) -> None:
        self.repo.delete_by_id(olt_id)

    def get_olt_by_ip(self, ip: str):
        return self.repo.get_by_ip(ip)

    def list_olt_ports(self, ip: str):
        return self.repo.list_ports_with_counts(ip)

    def list_recent_new_onu(self, limit: int = 50):
        return self.repo.list_recent_new_onu(limit=limit)

    def count_port_onus(self, ip: str, ifindex: str):
        return self.repo.count_onus_on_port(ip, ifindex)

    def list_port_onus(
        self,
        ip: str,
        ifindex: str,
        limit: int,
        offset: int,
    ):
        return self.repo.list_onus_on_port(ip, ifindex, limit=limit, offset=offset)

    _refresh_locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()
    _status_guard = threading.Lock()
    _last_poll_ok: dict[str, bool] = {}

    @classmethod
    def _lock_for_ip(cls, ip: str) -> threading.Lock:
        with cls._locks_guard:
            if ip not in cls._refresh_locks:
                cls._refresh_locks[ip] = threading.Lock()
            return cls._refresh_locks[ip]

    @classmethod
    def _set_poll_status(cls, ip: str, ok: bool) -> None:
        with cls._status_guard:
            cls._last_poll_ok[ip] = ok

    @classmethod
    def get_poll_status(cls, ip: str) -> bool | None:
        with cls._status_guard:
            return cls._last_poll_ok.get(ip)

    OID_IFNAME = "1.3.6.1.2.1.31.1.1.1.1"
    OID_GPON_BIND_SN = "1.3.6.1.4.1.3320.10.2.6.1.3"
    OID_GPON_ONU_SN_TAB = "1.3.6.1.4.1.3320.10.3.1.1.4"
    OID_GPON_STATUS = "1.3.6.1.4.1.3320.10.3.3.1.4"
    OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
    OID_TPLINK_SYS_DESCR = "1.3.6.1.4.1.11863.6.1.1.1.0"
    OID_TPLINK_SYS_NAME = "1.3.6.1.4.1.11863.6.1.1.2.0"
    OID_TPLINK_MODEL = "1.3.6.1.4.1.11863.6.1.1.5.0"
    OID_TPLINK_FIRMWARE = "1.3.6.1.4.1.11863.6.1.1.6.0"
    OID_TPLINK_CPU_USAGE = "1.3.6.1.4.1.11863.6.4.1.1.1.1.4.1"
    OID_TPLINK_MEM_USAGE = "1.3.6.1.4.1.11863.6.4.1.3.1.1.2.1"
    OID_TPLINK_TEMP = "1.3.6.1.4.1.11863.6.4.1.2.1.1.2.1"
    OID_TPLINK_PORT_NAMES = "1.3.6.1.4.1.11863.6.100.1.1.1.1.3"
    OID_TPLINK_ONU_SN = "1.3.6.1.4.1.11863.6.100.1.7.2.1.6"
    OID_TPLINK_ONLINE_STATUS = "1.3.6.1.4.1.11863.6.100.1.6.2.1.21"
    OID_TPLINK_ONU_RX = "1.3.6.1.4.1.11863.6.100.1.7.2.1.26"
    OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
    OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
    OID_CPU_USAGE = "1.3.6.1.4.1.3320.9.109.1.1.1.1.0"
    OID_CPU_USAGE_HOSTRES = "1.3.6.1.2.1.25.3.3.1.2"
    OID_MEM_USAGE = "1.3.6.1.4.1.3320.9.48.1.0"
    OID_TEMP_BOARD = "1.3.6.1.4.1.3320.9.181.1.1.7.0"

    def get_olt_info(self, ip: str) -> tuple[bool, dict[str, str]]:
        row = self.repo.get_by_ip(ip)
        if row is None:
            return False, {}

        info = {
            "ip": row["ip"],
            "vendor": (row["vendor"] or "-").upper(),
            "model": "-",
            "firmware": "-",
            "cpu": "-",
            "memory": "-",
            "temperature": "-",
        }

        vendor = (row["vendor"] or "").strip().lower()
        client = SnmpClient(timeout=1.0)
        if vendor == "tplink":
            sys_descr = self._get_str(client, row["ip"], row["community"], self.OID_TPLINK_SYS_DESCR)
            sys_name = self._get_str(client, row["ip"], row["community"], self.OID_TPLINK_SYS_NAME)
            model = self._get_str(client, row["ip"], row["community"], self.OID_TPLINK_MODEL) or self._parse_model(sys_descr, sys_name)
            firmware = self._get_str(client, row["ip"], row["community"], self.OID_TPLINK_FIRMWARE) or self._parse_firmware(sys_descr)
            cpu = self._get_int(client, row["ip"], row["community"], self.OID_TPLINK_CPU_USAGE)
            memory = self._get_float(client, row["ip"], row["community"], self.OID_TPLINK_MEM_USAGE)
            temperature = self._get_int(client, row["ip"], row["community"], self.OID_TPLINK_TEMP)
        else:
            sys_descr = self._get_str(client, row["ip"], row["community"], self.OID_SYS_DESCR)
            sys_name = self._get_str(client, row["ip"], row["community"], self.OID_SYS_NAME)
            model = self._parse_model(sys_descr, sys_name)
            firmware = self._parse_firmware(sys_descr)
            cpu = self._get_cpu_percent(client, row["ip"], row["community"])
            memory = self._get_int(client, row["ip"], row["community"], self.OID_MEM_USAGE)
            temperature = self._get_int(client, row["ip"], row["community"], self.OID_TEMP_BOARD)

        if model:
            info["model"] = model
        if firmware:
            info["firmware"] = firmware
        if cpu is not None:
            info["cpu"] = f"{cpu}%"
        if memory is not None:
            info["memory"] = f"{memory}%"
        if temperature is not None:
            info["temperature"] = f"{temperature}°C"
        return True, info

    def refresh_olt(self, ip: str) -> tuple[bool, str]:
        started = time.perf_counter()
        lock = self._lock_for_ip(ip)
        if not lock.acquire(blocking=False):
            logger.warning("refresh_olt skipped: lock busy ip=%s", ip)
            return False, "Обновление уже выполняется для этого OLT"

        try:
            row = self.repo.get_by_ip(ip)
            if row is None:
                logger.error("refresh_olt failed: olt not found ip=%s", ip)
                return False, "OLT не найден"

            logger.info("refresh_olt started ip=%s hostname=%s", row["ip"], row["hostname"])
            vendor = (row["vendor"] or "").strip().lower()
            if vendor == "tplink":
                return self._refresh_olt_tplink(row)

            client = SnmpClient(timeout=1.0)
            lines_ifname, ok_ifname = client.walk_with_status(
                host=row["ip"],
                community=row["community"],
                oid=self.OID_IFNAME,
            )
            lines_bind, ok_bind = client.walk_with_status(
                host=row["ip"],
                community=row["community"],
                oid=self.OID_GPON_BIND_SN,
            )
            lines_onu_sn, ok_onu_sn = client.walk_with_status(
                host=row["ip"],
                community=row["community"],
                oid=self.OID_GPON_ONU_SN_TAB,
            )
            lines_status, ok_status = client.walk_with_status(
                host=row["ip"],
                community=row["community"],
                oid=self.OID_GPON_STATUS,
            )

            if not any((ok_ifname, ok_bind, ok_onu_sn, ok_status)):
                logger.warning("refresh_olt failed: olt not responding ip=%s", row["ip"])
                self._set_poll_status(ip, False)
                return False, "OLT не отвечает по SNMP"

            ports = parse_ifname(lines_ifname)
            binds = parse_gpon_bind(lines_bind)
            sn_map = parse_onu_sn_table(lines_onu_sn)
            status_map = parse_onu_status_table(lines_status)
            online_sns = [sn_map[idx] for idx, st in status_map.items() if st == 3 and idx in sn_map]

            port_stats = self.repo.sync_ponports(ip, ports)
            if ok_bind:
                gpon_stats = self.repo.sync_gpon(ip, binds)
            else:
                gpon_stats = {"inserted": 0, "deleted": 0}
                logger.warning("refresh_olt: skip ONU sync because bind OID failed ip=%s", ip)
            if online_sns:
                self.repo.mark_onu_online(online_sns)
            self.repo.touch_refresh_time(ip)

            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            message = (
                "Обновление выполнено: "
                f"ports +{port_stats['inserted']}/~{port_stats['updated']}/-{port_stats['deleted']}, "
                f"onu +{gpon_stats['inserted']}/-{gpon_stats['deleted']}"
            )
            logger.info(
                "refresh_olt done ip=%s duration_ms=%s snmp_if_lines=%s snmp_bind_lines=%s ports=%s gpon=%s",
                ip,
                duration_ms,
                len(lines_ifname),
                len(lines_bind),
                port_stats,
                gpon_stats,
            )
            self._set_poll_status(ip, True)
            return True, message
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.exception("refresh_olt crashed ip=%s duration_ms=%s", ip, duration_ms)
            self._set_poll_status(ip, False)
            return False, "Ошибка обновления OLT (см. логи)"
        finally:
            lock.release()

    def _refresh_olt_tplink(self, row) -> tuple[bool, str]:
        ip = row["ip"]
        client = SnmpClient(timeout=1.0)

        lines_ifname, ok_ifname = client.walk_with_status(
            host=row["ip"],
            community=row["community"],
            oid=self.OID_IFNAME,
        )
        lines_ports, ok_ports = client.walk_with_status(
            host=row["ip"],
            community=row["community"],
            oid=self.OID_TPLINK_PORT_NAMES,
        )
        lines_sn, ok_sn = client.walk_with_status(
            host=row["ip"],
            community=row["community"],
            oid=self.OID_TPLINK_ONU_SN,
        )
        lines_online, ok_online = client.walk_with_status(
            host=row["ip"],
            community=row["community"],
            oid=self.OID_TPLINK_ONLINE_STATUS,
        )
        lines_rx, ok_rx = client.walk_with_status(
            host=row["ip"],
            community=row["community"],
            oid=self.OID_TPLINK_ONU_RX,
        )

        if not any((ok_ifname, ok_ports, ok_sn, ok_online, ok_rx)):
            logger.warning("refresh_olt failed: olt not responding ip=%s", ip)
            self._set_poll_status(ip, False)
            return False, "OLT не отвечает по SNMP"

        ports = parse_ifname(lines_ifname)
        ifindex_by_port_name = {name.strip().lower(): ifindex for ifindex, name in ports}
        ifindex_by_port_no: dict[str, str] = {}
        for ifindex, name in ports:
            m = re.search(r"gpon\s+\d+/\d+/(\d+)$", name.strip(), re.IGNORECASE)
            if m:
                ifindex_by_port_no[m.group(1)] = ifindex

        port_name_by_key = parse_tplink_keyed_strings(lines_ports, self.OID_TPLINK_PORT_NAMES)
        port_name_by_port_no: dict[str, str] = {}
        for key, port_name in port_name_by_key.items():
            parts = key.split(".")
            if not parts:
                continue
            port_name_by_port_no[parts[-1]] = port_name

        sn_by_key = parse_tplink_keyed_strings(lines_sn, self.OID_TPLINK_ONU_SN)
        online_by_key = parse_tplink_keyed_ints(lines_online, self.OID_TPLINK_ONLINE_STATUS)
        rx_by_key = parse_tplink_keyed_strings(lines_rx, self.OID_TPLINK_ONU_RX)
        candidates: list[dict[str, str | int]] = []
        for key, raw_sn in sn_by_key.items():
            parts = key.split(".")
            if len(parts) < 2:
                continue
            port_no, onu_id = parts[-2], parts[-1]
            port_name = (port_name_by_port_no.get(port_no) or f"GPON 1/1/{port_no}").strip()
            ifindex = ifindex_by_port_name.get(port_name.lower()) or ifindex_by_port_no.get(port_no)
            if not ifindex:
                continue
            sn = norm_sn(raw_sn)
            if len(sn) != 16:
                continue
            candidates.append(
                {
                    "key": key,
                    "ifindex": ifindex,
                    "onu_id": onu_id,
                    "sn": sn,
                    "online": int(online_by_key.get(key, 0)),
                    "has_rx": int(bool((rx_by_key.get(key) or "").strip())),
                }
            )

        # TP-Link may expose duplicate SN rows on multiple ports; keep best row per SN.
        # Priority: online row -> non-zero ONU id -> larger ONU id.
        best_by_sn: dict[str, dict[str, str | int]] = {}
        for c in candidates:
            sn = str(c["sn"])
            prev = best_by_sn.get(sn)
            if prev is None:
                best_by_sn[sn] = c
                continue
            prev_onu_id = str(prev["onu_id"])
            cur_onu_id = str(c["onu_id"])
            prev_score = (
                int(prev["online"]) == 1,
                int(prev["has_rx"]) == 1,
                prev_onu_id != "0",
                int(prev_onu_id) if prev_onu_id.isdigit() else -1,
            )
            cur_score = (
                int(c["online"]) == 1,
                int(c["has_rx"]) == 1,
                cur_onu_id != "0",
                int(cur_onu_id) if cur_onu_id.isdigit() else -1,
            )
            if cur_score > prev_score:
                best_by_sn[sn] = c

        binds = [(str(v["ifindex"]), str(v["onu_id"]), str(v["sn"])) for v in best_by_sn.values()]
        online_sns = [str(v["sn"]) for v in best_by_sn.values() if int(v["online"]) == 1]

        port_stats = self.repo.sync_ponports(ip, ports)
        if ok_sn:
            gpon_stats = self.repo.sync_gpon(ip, binds)
        else:
            gpon_stats = {"inserted": 0, "deleted": 0}
            logger.warning("refresh_olt tplink: skip ONU sync because SN table OID failed ip=%s", ip)
        if online_sns:
            self.repo.mark_onu_online(online_sns)
        self.repo.touch_refresh_time(ip)
        self._set_poll_status(ip, True)

        message = (
            "Обновление выполнено: "
            f"ports +{port_stats['inserted']}/~{port_stats['updated']}/-{port_stats['deleted']}, "
            f"onu +{gpon_stats['inserted']}/-{gpon_stats['deleted']}"
        )
        logger.info(
            "refresh_olt tplink done ip=%s ifname=%s ports_oid=%s sn=%s online=%s rx=%s ports=%s gpon=%s",
            ip,
            len(lines_ifname),
            len(lines_ports),
            len(lines_sn),
            len(lines_online),
            len(lines_rx),
            port_stats,
            gpon_stats,
        )
        return True, message

    def bounce_port(self, ip: str, ifindex: str) -> tuple[bool, str]:
        row = self.repo.get_by_ip(ip)
        if row is None:
            return False, "OLT не найден"

        client = SnmpClient(timeout=1.0)
        oid = f"{self.OID_IF_ADMIN_STATUS}.{ifindex}"
        ok_down = client.set(host=row["ip"], community=row["community"], oid=oid, typechar="i", value="2")
        time.sleep(1)
        ok_up = client.set(host=row["ip"], community=row["community"], oid=oid, typechar="i", value="1")
        ok = ok_down and ok_up
        if ok:
            logger.info("bounce_port done ip=%s ifindex=%s", ip, ifindex)
            return True, "Порт перезагружен (down→up)"
        logger.warning("bounce_port failed ip=%s ifindex=%s down=%s up=%s", ip, ifindex, ok_down, ok_up)
        return False, "Не удалось перезагрузить порт"

    def _get_int(self, client: SnmpClient, ip: str, community: str, oid: str) -> int | None:
        lines = client.get(ip, community, oid)
        if not lines:
            lines = client.walk(ip, community, oid)
        for ln in lines:
            m = re.search(r"(-?\d+)\s*$", ln)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
        return None

    def _get_str(self, client: SnmpClient, ip: str, community: str, oid: str) -> str | None:
        lines = client.get(ip, community, oid)
        if not lines:
            lines = client.walk(ip, community, oid)
        for ln in lines:
            m = re.search(r"STRING:\s*\"([^\"]*)\"", ln)
            if m:
                return m.group(1).strip() or None
            m = re.search(r"STRING:\s*\"?(.*)$", ln)
            if m:
                return m.group(1).strip().strip('"') or None
            if "=" in ln:
                value = ln.split("=", 1)[1].strip()
                if value:
                    return value
        return None

    def _get_float(self, client: SnmpClient, ip: str, community: str, oid: str) -> float | None:
        lines = client.get(ip, community, oid)
        if not lines:
            lines = client.walk(ip, community, oid)
        for ln in lines:
            m = re.search(r"(-?\d+(?:\.\d+)?)\s*$", ln)
            if not m:
                continue
            try:
                return float(m.group(1))
            except ValueError:
                continue
        return None

    def _get_cpu_percent(self, client: SnmpClient, ip: str, community: str) -> int | None:
        direct = self._get_int(client, ip, community, self.OID_CPU_USAGE)
        if direct is not None:
            return direct

        vals: list[int] = []
        for ln in client.walk(ip, community, self.OID_CPU_USAGE_HOSTRES):
            m = re.search(r"(-?\d+)\s*$", ln)
            if not m:
                continue
            try:
                vals.append(int(m.group(1)))
            except ValueError:
                continue
        if vals:
            return round(sum(vals) / len(vals))
        return None

    def _parse_model(self, sys_descr: str | None, sys_name: str | None) -> str | None:
        text = " ".join(p for p in [sys_descr, sys_name] if p)
        if not text:
            return None
        m = re.search(r"\b(GP\d{4,5}[-\w]*)\b", text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        m = re.search(r"\b(EPON[-\w]+)\b", text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        return text.split(",")[0].strip()[:48] or None

    def _parse_firmware(self, sys_descr: str | None) -> str | None:
        if not sys_descr:
            return None
        patterns = [
            r"(?:Software|Firmware|Version)\s*[:=]\s*([^\s,;]+)",
            r"\bVersion\s+([^\s,;]+)",
            r"\b(V\d+(?:\.\d+){1,4}[^\s,;]*)\b",
        ]
        for pat in patterns:
            m = re.search(pat, sys_descr, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None
