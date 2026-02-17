from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from gpon_turon.db import get_db
from gpon_turon.repositories import OnuRepository
from gpon_turon.services import OnuService


bp = Blueprint("onu", __name__)


def _service() -> OnuService:
    settings = current_app.config["SETTINGS"]
    conn = get_db(settings.db_path)
    return OnuService(OnuRepository(conn))


@bp.post("/search")
def search():
    query = (request.form.get("q") or "").strip()
    if not query:
        return redirect(url_for("olts.home"))

    sn_norm, row, _ = _service().find_by_sn(query)
    if len(sn_norm) != 16:
        flash("Введите корректный SN (16 HEX символов)", "error")
        return redirect(url_for("olts.home"))
    if row is None:
        return render_template("not_found.html", sn=sn_norm)
    return redirect(url_for("onu.onu_by_sn", sn=sn_norm))


@bp.get("/onu/sn/<sn>")
def onu_by_sn(sn: str):
    sn_norm, row, vendor = _service().find_by_sn(sn)
    if len(sn_norm) != 16 or row is None:
        return render_template("not_found.html", sn=sn_norm)
    metrics = _service().get_live_metrics(row, sn_norm)
    port_base = row["port_name"] or row["portonu"]
    port_display = f"{port_base}:{row['idonu']}"
    last_online = row["last_online"] if metrics["status"] == "OFFLINE" else None
    return render_template(
        "onu.html",
        sn=sn_norm,
        onu=row,
        onu_vendor=vendor,
        metrics=metrics,
        port_display=port_display,
        last_online=last_online,
    )


@bp.post("/onu/sn/<sn>/reboot")
def reboot_onu(sn: str):
    svc = _service()
    sn_norm, row, _ = svc.find_by_sn(sn)
    if len(sn_norm) != 16 or row is None:
        flash("ONU не найдена", "error")
        return redirect(url_for("olts.home"))

    ok, message = svc.reboot_onu(row, sn_norm)
    flash(message, "success" if ok else "error")
    return redirect(url_for("onu.onu_by_sn", sn=sn_norm))
