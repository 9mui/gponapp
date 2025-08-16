import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import app
import snmp


def test_get_sys_uptime_ticks_parses_integer(monkeypatch):
    lines = ['SNMPv2-MIB::sysUpTime.0 = INTEGER: 1234']

    def fake_snmpwalk(host, community, oid, timeout=2):
        return lines if oid == '1.3.6.1.2.1.1.3.0' else []

    monkeypatch.setattr(app, 'snmpwalk', fake_snmpwalk)
    monkeypatch.setattr(snmp, 'snmpwalk', fake_snmpwalk)

    assert app.get_sys_uptime_ticks('1.2.3.4', 'public') == 1234
