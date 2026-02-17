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
)

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

        client = SnmpClient(timeout=1.0)
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
