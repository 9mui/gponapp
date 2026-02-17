import sqlite3


class OltRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, hostname, ip, community, vendor, last_refresh_at
            FROM olts
            ORDER BY id
            """
        ).fetchall()

    def get_by_ip(self, ip: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, hostname, ip, community, vendor, last_refresh_at
            FROM olts
            WHERE ip = ?
            LIMIT 1
            """,
            (ip,),
        ).fetchone()

    def list_ports_with_counts(self, ip: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                p.ifindex,
                p.name,
                COUNT(g.id) AS onu_count
            FROM ponports p
            LEFT JOIN gpon g
              ON g.olt_ip = p.olt_ip
             AND g.portonu = p.ifindex
            WHERE p.olt_ip = ?
              AND UPPER(p.name) LIKE 'GPON%/%'
              AND p.name NOT LIKE '%:%'
            GROUP BY p.ifindex, p.name
            ORDER BY CAST(p.ifindex AS INTEGER)
            """,
            (ip,),
        ).fetchall()

    def count_onus_on_port(self, ip: str, ifindex: str) -> int:
        sql = """
            SELECT COUNT(*)
            FROM gpon g
            WHERE g.olt_ip = ?
              AND g.portonu = ?
        """
        params: list[str] = [ip, ifindex]
        row = self.conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def list_onus_on_port(
        self,
        ip: str,
        ifindex: str,
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT
                g.snonu,
                g.idonu
            FROM gpon g
            WHERE g.olt_ip = ?
              AND g.portonu = ?
        """
        params: list[str | int] = [ip, ifindex]
        sql += " ORDER BY CAST(g.idonu AS INTEGER) LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self.conn.execute(sql, params).fetchall()

    def create(self, hostname: str, ip: str, community: str, vendor: str) -> None:
        self.conn.execute(
            """
            INSERT INTO olts(hostname, ip, community, vendor)
            VALUES(?, ?, ?, ?)
            """,
            (hostname, ip, community, vendor),
        )

    def delete_by_id(self, olt_id: int) -> None:
        row = self.conn.execute("SELECT ip FROM olts WHERE id = ?", (olt_id,)).fetchone()
        if not row:
            return

        olt_ip = row["ip"]
        self.conn.execute("DELETE FROM gpon WHERE olt_ip = ?", (olt_ip,))
        self.conn.execute("DELETE FROM ponports WHERE olt_ip = ?", (olt_ip,))
        self.conn.execute("DELETE FROM olts WHERE id = ?", (olt_id,))

    def touch_refresh_time(self, ip: str) -> None:
        self.conn.execute(
            "UPDATE olts SET last_refresh_at = CURRENT_TIMESTAMP WHERE ip = ?",
            (ip,),
        )

    def sync_ponports(self, ip: str, items: list[tuple[str, str]]) -> dict[str, int]:
        existing_rows = self.conn.execute(
            "SELECT ifindex, name FROM ponports WHERE olt_ip = ?",
            (ip,),
        ).fetchall()
        existing = {r["ifindex"]: r["name"] for r in existing_rows}
        incoming = {ifindex: name for ifindex, name in items}

        to_delete = [ifindex for ifindex in existing if ifindex not in incoming]
        to_insert = [(ip, ifindex, name) for ifindex, name in incoming.items() if ifindex not in existing]
        to_update = [(name, ip, ifindex) for ifindex, name in incoming.items() if ifindex in existing and existing[ifindex] != name]

        if to_delete:
            self.conn.executemany(
                "DELETE FROM ponports WHERE olt_ip = ? AND ifindex = ?",
                [(ip, ifindex) for ifindex in to_delete],
            )
        if to_insert:
            self.conn.executemany(
                "INSERT INTO ponports(olt_ip, ifindex, name) VALUES(?, ?, ?)",
                to_insert,
            )
        if to_update:
            self.conn.executemany(
                "UPDATE ponports SET name = ? WHERE olt_ip = ? AND ifindex = ?",
                to_update,
            )

        return {
            "inserted": len(to_insert),
            "updated": len(to_update),
            "deleted": len(to_delete),
        }

    def sync_gpon(self, ip: str, items: list[tuple[str, str, str]]) -> dict[str, int]:
        existing_rows = self.conn.execute(
            "SELECT portonu, idonu, snonu FROM gpon WHERE olt_ip = ?",
            (ip,),
        ).fetchall()
        existing = {(r["portonu"], r["idonu"], r["snonu"]) for r in existing_rows}
        incoming = {(portonu, idonu, snonu) for portonu, idonu, snonu in items}

        # If an ONU appears on this OLT now, remove stale records for the same SN from other OLTs.
        # This prevents duplicates when an ONU is moved by field operations.
        incoming_sns = sorted({s for _, _, s in incoming})
        globally_known_sns: set[str] = set()
        if incoming_sns:
            batch_size = 200
            for i in range(0, len(incoming_sns), batch_size):
                batch = incoming_sns[i:i + batch_size]
                placeholders = ",".join("?" for _ in batch)
                rows = self.conn.execute(
                    f"""
                    SELECT DISTINCT REPLACE(UPPER(snonu), ' ', '') AS sn_norm
                    FROM gpon
                    WHERE REPLACE(UPPER(snonu), ' ', '') IN ({placeholders})
                    """,
                    batch,
                ).fetchall()
                globally_known_sns.update(r["sn_norm"] for r in rows)

        if incoming_sns:
            self._delete_cross_olt_duplicates(current_ip=ip, sn_list=incoming_sns)

        to_delete = sorted(existing - incoming)
        to_insert = sorted(incoming - existing)

        if to_delete:
            self.conn.executemany(
                "DELETE FROM gpon WHERE olt_ip = ? AND portonu = ? AND idonu = ? AND snonu = ?",
                [(ip, p, o, s) for p, o, s in to_delete],
            )
        if to_insert:
            self.conn.executemany(
                "INSERT INTO gpon(olt_ip, portonu, idonu, snonu) VALUES(?, ?, ?, ?)",
                [(ip, p, o, s) for p, o, s in to_insert],
            )

            truly_new = [(s, ip, p, o) for p, o, s in to_insert if s not in globally_known_sns]
            if truly_new:
                self._record_recent_new_onu(truly_new)

        if incoming:
            sn_rows = sorted({s for _, _, s in incoming})
            self.conn.executemany(
                """
                INSERT INTO onu_seen(sn_norm)
                VALUES(?)
                ON CONFLICT(sn_norm) DO UPDATE SET last_seen = CURRENT_TIMESTAMP
                """,
                [(sn,) for sn in sn_rows],
            )

        return {
            "inserted": len(to_insert),
            "deleted": len(to_delete),
        }

    def _delete_cross_olt_duplicates(self, current_ip: str, sn_list: list[str]) -> None:
        if not sn_list:
            return
        # Keep margin below SQLite variable limit.
        batch_size = 200
        for i in range(0, len(sn_list), batch_size):
            batch = sn_list[i:i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            self.conn.execute(
                f"""
                DELETE FROM gpon
                WHERE olt_ip != ?
                  AND REPLACE(UPPER(snonu), ' ', '') IN ({placeholders})
                """,
                (current_ip, *batch),
            )

    def _record_recent_new_onu(self, new_rows: list[tuple[str, str, str, str]]) -> None:
        """
        new_rows item: (sn_norm, olt_ip, portonu, idonu)
        Keep only latest 50 unique SN records.
        """
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO recent_new_onu(sn_norm, olt_ip, portonu, idonu)
            VALUES(?, ?, ?, ?)
            """,
            new_rows,
        )
        self.conn.execute(
            """
            DELETE FROM recent_new_onu
            WHERE id IN (
                SELECT id
                FROM recent_new_onu
                ORDER BY datetime(first_seen) DESC, id DESC
                LIMIT -1 OFFSET 50
            )
            """
        )

    def list_recent_new_onu(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                r.id,
                r.sn_norm,
                r.first_seen,
                r.olt_ip,
                r.portonu,
                r.idonu,
                p.name AS port_name
            FROM recent_new_onu r
            LEFT JOIN ponports p
              ON p.olt_ip = r.olt_ip
             AND p.ifindex = r.portonu
            ORDER BY datetime(r.first_seen) DESC, r.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def mark_onu_online(self, sn_list: list[str]) -> None:
        if not sn_list:
            return
        unique = sorted(set(sn_list))
        self.conn.executemany(
            """
            INSERT INTO onu_seen(sn_norm, last_online, status)
            VALUES(?, CURRENT_TIMESTAMP, 3)
            ON CONFLICT(sn_norm) DO UPDATE SET
              last_online = CURRENT_TIMESTAMP,
              status = 3
            """,
            [(sn,) for sn in unique],
        )
