import sqlite3


class OnuRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def find_by_sn_norm(self, sn_norm: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                g.olt_ip,
                g.portonu,
                g.idonu,
                g.snonu,
                o.hostname AS olt_hostname,
                o.community AS olt_community,
                p.name AS port_name,
                s.last_seen AS last_seen,
                s.last_online AS last_online
            FROM gpon g
            LEFT JOIN olts o ON o.ip = g.olt_ip
            LEFT JOIN ponports p
              ON p.olt_ip = g.olt_ip
             AND p.ifindex = g.portonu
            LEFT JOIN onu_seen s
              ON s.sn_norm = REPLACE(UPPER(g.snonu), ' ', '')
            WHERE REPLACE(UPPER(g.snonu), ' ', '') = ?
            LIMIT 1
            """,
            (sn_norm,),
        ).fetchone()
