import ipaddress
import sqlite3
import logging
import threading

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from gpon_turon.db import get_db
from gpon_turon.repositories import OltRepository
from gpon_turon.services import OltService


bp = Blueprint("olts", __name__)
logger = logging.getLogger(__name__)
_refresh_all_lock = threading.Lock()


def _service() -> OltService:
    settings = current_app.config["SETTINGS"]
    conn = get_db(settings.db_path)
    repo = OltRepository(conn)
    return OltService(repo)


@bp.get("/")
def home():
    svc = _service()
    rows = []
    for row in svc.list_olts():
        item = dict(row)
        item["poll_ok"] = svc.get_poll_status(row["ip"])
        rows.append(item)
    return render_template("index.html", olts=rows)


@bp.get("/onus/new")
def recent_new_onu():
    rows = _service().list_recent_new_onu(limit=50)
    return render_template("new_onus.html", items=rows)


@bp.post("/olts/refresh-all")
def refresh_all_olts():
    started, total, ok_count, fail_count, failed_ips = run_refresh_all_once()
    if not started:
        flash("Обновление всех OLT уже выполняется", "error")
        return redirect(url_for("olts.home"))
    if total == 0:
        flash("Нет OLT для обновления", "error")
        return redirect(url_for("olts.home"))

    flash(f"Обновление завершено: успешно {ok_count}, с ошибкой {fail_count}", "success" if fail_count == 0 else "error")
    if failed_ips:
        preview = ", ".join(failed_ips[:5])
        tail = " ..." if len(failed_ips) > 5 else ""
        flash(f"Не удалось обновить: {preview}{tail}", "error")
    return redirect(url_for("olts.home"))


def run_refresh_all_once() -> tuple[bool, int, int, int, list[str]]:
    """
    Runs refresh for all OLTs once.
    Returns: (started, total, success, failed, failed_ips)
    started=False means skipped because another cycle is running.
    """
    if not _refresh_all_lock.acquire(blocking=False):
        return False, 0, 0, 0, []

    try:
        svc = _service()
        rows = svc.list_olts()
        if not rows:
            return True, 0, 0, 0, []

        ok_count = 0
        fail_count = 0
        failed_ips: list[str] = []
        for row in rows:
            ok, _ = svc.refresh_olt(row["ip"])
            if ok:
                get_db(current_app.config["SETTINGS"].db_path).commit()
                ok_count += 1
            else:
                get_db(current_app.config["SETTINGS"].db_path).rollback()
                fail_count += 1
                failed_ips.append(row["ip"])

        logger.info(
            "refresh_all_olts done total=%s success=%s failed=%s failed_ips=%s",
            len(rows),
            ok_count,
            fail_count,
            failed_ips,
        )
        return True, len(rows), ok_count, fail_count, failed_ips
    finally:
        _refresh_all_lock.release()


@bp.post("/olts/add")
def add_olt():
    hostname = request.form.get("hostname", "")
    ip = request.form.get("ip", "")
    community = request.form.get("community", "private")
    vendor = request.form.get("vendor", "bdcom")

    hostname = hostname.strip()
    ip = ip.strip()
    community = community.strip() or "private"
    vendor = (vendor or "bdcom").strip().lower()

    if not hostname or not ip:
        flash("Hostname и IP обязательны", "error")
        return redirect(url_for("olts.home"))

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        flash("Некорректный IP адрес", "error")
        return redirect(url_for("olts.home"))

    svc = _service()
    try:
        svc.add_olt(hostname=hostname, ip=ip, community=community, vendor=vendor)
        get_db(current_app.config["SETTINGS"].db_path).commit()
        flash("OLT добавлен", "success")
    except sqlite3.IntegrityError:
        get_db(current_app.config["SETTINGS"].db_path).rollback()
        flash(f"OLT с IP {ip} уже существует", "error")
    except Exception as exc:
        get_db(current_app.config["SETTINGS"].db_path).rollback()
        flash(f"Ошибка добавления OLT: {exc}", "error")

    return redirect(url_for("olts.home"))


@bp.post("/olts/<int:olt_id>/delete")
def delete_olt(olt_id: int):
    svc = _service()
    try:
        svc.delete_olt(olt_id)
        get_db(current_app.config["SETTINGS"].db_path).commit()
        flash("OLT удален", "success")
    except Exception as exc:
        get_db(current_app.config["SETTINGS"].db_path).rollback()
        flash(f"Ошибка удаления OLT: {exc}", "error")
    return redirect(url_for("olts.home"))


@bp.get("/olt/<ip>")
def olt_ports(ip: str):
    svc = _service()
    olt = svc.get_olt_by_ip(ip)
    if olt is None:
        flash("OLT не найден", "error")
        return redirect(url_for("olts.home"))

    ports = svc.list_olt_ports(ip)
    return render_template("olt_ports.html", olt=olt, ports=ports)


@bp.get("/olt/<ip>/info")
def olt_info(ip: str):
    svc = _service()
    ok, info = svc.get_olt_info(ip)
    if not ok:
        flash("OLT не найден", "error")
        return redirect(url_for("olts.home"))
    return render_template("olt_info.html", info=info)


@bp.post("/olt/<ip>/refresh")
def refresh_olt(ip: str):
    svc = _service()
    ok, message = svc.refresh_olt(ip)
    if ok:
        get_db(current_app.config["SETTINGS"].db_path).commit()
        flash(message, "success")
    else:
        get_db(current_app.config["SETTINGS"].db_path).rollback()
        flash(message, "error")
    return redirect(url_for("olts.olt_ports", ip=ip))


@bp.post("/olt/<ip>/port/<ifindex>/bounce")
def bounce_port(ip: str, ifindex: str):
    svc = _service()
    ok, message = svc.bounce_port(ip, ifindex)
    flash(message, "success" if ok else "error")
    return redirect(url_for("olts.olt_ports", ip=ip))


@bp.get("/olt/<ip>/port/<ifindex>")
def olt_port_onus(ip: str, ifindex: str):
    svc = _service()
    olt = svc.get_olt_by_ip(ip)
    if olt is None:
        flash("OLT не найден", "error")
        return redirect(url_for("olts.home"))

    ports = svc.list_olt_ports(ip)
    port_name = next((p["name"] for p in ports if str(p["ifindex"]) == str(ifindex)), ifindex)
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 50

    total = svc.count_port_onus(ip, ifindex)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page

    onus = svc.list_port_onus(ip, ifindex, limit=per_page, offset=offset)
    return render_template(
        "port_onus.html",
        olt=olt,
        ifindex=ifindex,
        port_name=port_name,
        onus=onus,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )
