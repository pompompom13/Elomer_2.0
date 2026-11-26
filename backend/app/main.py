"""ЭЛОМЕР — API планировщика полевой работы."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, Optional
from math import ceil
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import cities as C
from . import facilities as F
from . import norms as N
from .care import CareFilter
from .export_xlsx import build_workbook
from . import base_export
from .networks import annotate, summary as network_summary
from .doctor_stats import calibration
from .coverage import required_team, specialty_targets
from .planner import optimize_plan, plan_project, preview_territories
from .goal import options as goal_options, team as goal_team
from . import projects as P
from . import plan_results, project_xlsx, report_pdf
from . import field_data as FD

app = FastAPI(title="ЭЛОМЕР", version="12.0")
# ответы с точками карты (десятки тысяч учреждений) сжимаются — в 5–8 раз меньше трафика
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
app.add_middleware(GZipMiddleware, minimum_size=2048)


@app.on_event("startup")
def _warm_base() -> None:
    """Прогрев «Базы» в фоне: врачи всех специальностей по ЛПУ считаются ~1 мин, дальше — из памяти."""
    import threading

    def run():
        try:  # загрузки, прерванные перезапуском сервера, продолжаются сами
            from . import resume
            resume.resume_all()
        except Exception:  # noqa: BLE001
            pass
        try:
            base_export.summary(BaseQuery(scope="region").model_dump())
            base_export.alld_save()
        except Exception:  # noqa: BLE001 — прогрев не должен ронять сервер
            pass
    threading.Thread(target=run, daemon=True).start()


class PlanRequest(BaseModel):
    city: str
    specialty: str = "Кардиолог"
    reps: int = Field(1, ge=1, le=80)
    doctor_visits_per_day: int = Field(5, ge=0, le=20)
    pharmacy_visits_per_day: int = Field(3, ge=0, le=20)
    doctor_frequency: int = Field(2, ge=1, le=12)
    pharmacy_frequency: int = Field(1, ge=1, le=12)
    frequency_unit: Literal["project_month", "calendar_month", "month", "project"] = "project_month"
    start_date: date
    months: int = Field(3, ge=1, le=12)
    end_date: Optional[date] = None
    transport: Literal["car", "public", "walk"] = "car"
    day_start: str = Field("09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    workday_limit_h: float = Field(8.0, ge=4, le=12)
    travel_time_factor: float = Field(1.0, ge=0.5, le=3)
    travel_detour: Optional[float] = Field(None, ge=1, le=3)
    travel_overhead_min: Optional[float] = Field(None, ge=0, le=60)
    wait_min: Optional[float] = Field(None, ge=0, le=180)
    doctor_visit_min: Optional[float] = Field(None, ge=1, le=180)
    pharmacy_visit_min: Optional[float] = Field(None, ge=1, le=180)
    pharmacy_wait_min: Optional[float] = Field(None, ge=0, le=180)
    lpu_entry_min: Optional[float] = Field(None, ge=0, le=180)
    lunch_min: Optional[float] = Field(None, ge=0, le=180)
    crm_min: Optional[float] = Field(None, ge=0, le=180)
    start_points: list[Optional[list[float]]] = []
    doctor_overrides: dict[str, int] = {}
    excluded_ids: list[str] = []
    categories: Optional[list[str]] = None
    lpu_care: CareFilter = "all"
    lpu_ownership: Optional[list[str]] = None        # ["state", "private"]
    pharmacy_ownership: Optional[list[str]] = None
    lpu_networks: Optional[list[str]] = None         # ключи сетей частных клиник; None — все
    pharmacy_networks: Optional[list[str]] = None    # ключи аптечных сетей; "" — аптеки без сети
    territories: Optional[dict] = None               # {"centers": [[lat, lon]], "weights": [...]} — границы с карты
    coverage: str = "spread"                         # spread — по всей зоне, compact — ближе к точке старта
    pharmacy_priority: str = "near"                  # near — ближайшие, turnover — с большим оборотом
    extra_specialties: list[dict] = []               # [{specialty, doctor_visits_per_day, doctor_frequency}]
    project_mix: bool = False
    specialty_targets: Optional[dict[str, int]] = None
    pharmacy_target: Optional[int] = Field(None, ge=0)
    team_goal_doctors: bool = False
    team_goal_pharmacies: bool = False
    doctor_target: Optional[int] = Field(None, ge=0)
    doctor_override_specialty: Optional[str] = None
    client_base: Optional[str] = None                # ЛПУ из базы заказчика вместо наших
    pharmacy_min_turnover: Optional[int] = None      # только аптеки с оборотом не ниже, руб./мес

    @model_validator(mode="after")
    def check_team_size(self):
        if not self.project_mix and self.reps > 30:
            raise ValueError("В отдельном плане города можно указать до 30 медпредов.")
        return self


def _plan(req: PlanRequest, optimize: bool = False) -> dict:
    p = req.model_dump()
    p["start_date"] = req.start_date.isoformat()
    p["end_date"] = req.end_date.isoformat() if req.end_date else None
    if req.specialty not in N.SPECIALTIES:
        raise HTTPException(400, f"Неизвестная специальность: {req.specialty}")
    if req.transport not in N.TRANSPORT:
        raise HTTPException(400, "Неизвестный вид транспорта")
    try:
        if req.team_goal_doctors or req.team_goal_pharmacies:
            probe = plan_project(p | {"project_mix": True, "reps": 1, "territories": None})
            summary = probe["summary"]
            rows = summary["specialties"]
            doctors = req.doctor_target if req.doctor_target is not None else summary["available_doctors"]
            pharmacies = req.pharmacy_target if req.pharmacy_target is not None else summary["available_pharmacies"]
            quotas = specialty_targets(rows, doctors) if req.team_goal_doctors else None
            ph_capacity = summary["pharmacy_capacity_per_rep"]
            pharmacy_reps = ceil(min(pharmacies, summary["available_pharmacies"]) / ph_capacity) if req.team_goal_pharmacies and ph_capacity else 1
            size = min(80, max(required_team(rows, quotas), pharmacy_reps))
            return optimize_plan(p | {"project_mix": True, "reps": size, "territories": None, "specialty_targets": quotas,
                                      "pharmacy_target": pharmacies if req.team_goal_pharmacies else None,
                                      "team_goal_doctors": False, "team_goal_pharmacies": False}, auto_team=True)
        return optimize_plan(p) if optimize else plan_project(p)
    except KeyError:
        raise HTTPException(404, "Город не загружен. Загрузите базу учреждений.")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/meta")
def meta():
    from .advisor import benchmarks
    return {**N.defaults(), "categories": F.CATEGORY_LABELS, "benchmarks": benchmarks()}


@app.get("/api/cities")
def cities():
    return C.catalog()


@app.post("/api/cities/{slug}/load")
def load_city(slug: str):
    """Загрузить (или обновить) базу города из OpenStreetMap. Крупный город — до 2–3 минут."""
    try:
        ds = C.load(slug)
    except KeyError:
        raise HTTPException(404, "Города нет в справочнике")
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"slug": ds["slug"], "count": len(ds["facilities"])}


# ─── Базы заказчиков ─────────────────────────────────────────────────────────

@app.get("/api/client-bases")
def client_bases_list(project_id: str = ""):
    from . import client_bases as CB
    return CB.list_all(project_id or None)


@app.post("/api/client-bases")
async def client_bases_import(name: str = Form(""), project_id: str = Form(""), files: list[UploadFile] = File(...)):
    """Загрузить базу заказчика: она хранится отдельно и сверяется с основной базой."""
    from . import client_bases as CB
    data = [(f.filename or "file.xlsx", await f.read()) for f in files]
    return CB.start_import(name or (files[0].filename or "База заказчика"), project_id or None, data)


@app.get("/api/client-bases/{bid}/status")
def client_bases_status(bid: str):
    from . import client_bases as CB
    return CB.status(bid)


@app.get("/api/client-bases/{bid}/report")
def client_bases_report(bid: str):
    from . import client_bases as CB
    try:
        return CB.report(bid)
    except KeyError:
        raise HTTPException(404, "База заказчика не найдена")


class ReviewBody(BaseModel):
    action: str = "accept"            # accept | reject
    ids: Optional[list[str]] = None   # None — все ожидающие проверки
    doctors: Optional[list[dict]] = None  # [{id, spec}] — принять числа врачей


@app.post("/api/client-bases/{bid}/review")
def client_bases_review(bid: str, body: ReviewBody):
    from . import client_bases as CB
    try:
        return CB.review(bid, body.ids, body.action, body.doctors)
    except KeyError:
        raise HTTPException(404, "База заказчика не найдена")


@app.get("/api/client-bases/{bid}/xlsx")
def client_bases_xlsx(bid: str):
    from . import client_bases as CB
    try:
        d = CB.get(bid)
    except KeyError:
        raise HTTPException(404, "База заказчика не найдена")
    return _file(CB.report_xlsx(bid), f"Сверка_{d['name']}.xlsx"[:120], XLSX)


@app.delete("/api/client-bases/{bid}")
def client_bases_delete(bid: str):
    from . import client_bases as CB
    CB.delete(bid)
    return {"ok": True}


@app.get("/api/territory")
def territory_status():
    from . import territory as T
    return T.status()


@app.post("/api/territory/suburbs")
def territory_suburbs(retry: bool = False):
    """Загрузить ЛПУ пригородов всех городов (в фоне); retry — дозагрузить недокачанные части колец."""
    from . import territory as T
    return T.start_suburbs(retry=retry)


@app.get("/api/dental")
def dental_status():
    """Сколько городов уже со стоматологиями и ход фоновой дозагрузки."""
    from . import dental as D
    return D.status()


@app.post("/api/dental/load")
def dental_load():
    """Дозагрузить стоматологии во все загруженные города (фоном, по одному запросу на город)."""
    from . import dental as D
    return D.start()


@app.get("/api/lpu-registry")
def lpu_registry_status():
    """Реестр ЛПУ: загружен ли, ход загрузки и итог последней."""
    from . import lpu_registry as LR
    return LR.status()


@app.post("/api/lpu-registry")
async def lpu_registry_upload(file: UploadFile = File(...)):
    """Загрузить выгрузку ЛПУ (xlsx): ЛПУ городов и пригородов заменяются учреждениями реестра (в фоне)."""
    from . import lpu_registry as LR
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Нужен файл Excel (.xlsx) с выгрузкой ЛПУ")
    return LR.start(await file.read())


@app.post("/api/territory/pharmacies")
def territory_pharmacies():
    """Пересобрать аптеки вне городов из реестра."""
    from . import territory as T
    return T.build_pharmacies()


@app.get("/api/preload")
def preload_status():
    return C.preload_status()


@app.post("/api/preload")
def preload_start():
    return C.start_preload()


@app.get("/api/cities/{slug}")
def city(slug: str, specialty: str = "Кардиолог", client_base: str = ""):
    try:
        ds = F.load_city(slug)
    except KeyError:
        raise HTTPException(404, "Город не найден")
    over = {}
    if client_base:
        from . import client_bases as CB
        try:
            ds = CB.with_client(ds, client_base)
        except KeyError:
            raise HTTPException(404, "База заказчика не найдена")
        over = CB.doctor_counts(ds["facilities"], slug, specialty)
    annotate(ds["facilities"])
    cal = calibration(slug, specialty)
    for f in ds["facilities"]:
        f["est"] = (over[f["id"]] if f["id"] in over else len(N.doctors_in(f, specialty, calib=cal))) if f["kind"] == "lpu" else 0
    ds["doctors_stat"] = {"method": cal["method"], "target": cal["target"], "base": cal["base"]}
    ds["networks"] = network_summary(ds["facilities"])
    # города, загруженные пакетом, дозаполняют адреса при первом открытии
    if slug not in F._geocode_jobs and any(not f["address"] or f.get("needs_coords") for f in ds["facilities"]):
        F.start_geocoding(slug)
    return {**ds, "geocoding": F.geocode_status(slug)}


@app.get("/api/cities/{slug}/facility/{fid}")
def facility_card(slug: str, fid: str):
    """Карточка учреждения: сведения и оценка врачей по всем специальностям."""
    try:
        ds = F.load_city(slug)
    except KeyError:
        raise HTTPException(404, "Город не загружен")
    f = next((x for x in annotate(ds["facilities"]) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404, "Учреждение не найдено")
    return base_export.facility_card(f, slug)


@app.post("/api/plan/routes")
def plan_routes(req: PlanRequest):
    """Маршрутные листы: по листу на медпреда, дни по неделям, порядок объезда и время."""
    from .export_xlsx import route_sheets
    result = _plan(req)
    s = result["summary"]
    return _file(route_sheets(result), f"Маршрутные_листы_{s['city']}_{s['start']}.xlsx", XLSX)


@app.get("/api/cities/{slug}/stats")
def city_stats(slug: str, lpu_care: CareFilter = "all", client_base: str = ""):
    from .city_stats import city_stats as _stats
    try:
        return _stats(slug, lpu_care, client_base)
    except KeyError:
        raise HTTPException(404, "Город не загружен")


@app.get("/api/cities/{slug}/geocoding")
def geocoding(slug: str):
    return F.geocode_status(slug)


@app.post("/api/cities/{slug}/import")
async def import_base(slug: str, file: UploadFile = File(...), replace: bool = Form(False),
                      city_name: str = Form("")):
    content = await file.read()
    try:
        return F.import_table(slug, city_name or slug, content, file.filename or "base.xlsx", replace)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/import-template")
def template():
    return Response(F.import_template(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=elomer_base_template.xlsx"})


@app.post("/api/plan")
def plan(req: PlanRequest):
    return plan_results.register(_plan(req, optimize=True))


@app.get("/api/plan/results/{calculation_id}/{kind}")
def export_plan_result(calculation_id: str, kind: str):
    from .export_xlsx import route_sheets
    try:
        result = plan_results.get(calculation_id)
    except KeyError:
        raise HTTPException(404, "Этот расчёт больше не открыт. Пересчитайте план или откройте его из проекта.")
    city = result["summary"]["city"]
    if kind == "xlsx":
        return _file(build_workbook(result), f"План_{city}.xlsx", XLSX)
    if kind == "routes":
        return _file(route_sheets(result), f"Маршрутные_листы_{city}.xlsx", XLSX)
    if kind == "pdf":
        return _file(report_pdf.plan_pdf(result), f"Отчёт_{city}.pdf", "application/pdf")
    raise HTTPException(404, "Неизвестный формат выгрузки.")


@app.post("/api/territories")
def territories(req: PlanRequest):
    """Зоны медпредов без расчёта плана — для правки границ на карте."""
    p = req.model_dump()
    p["start_date"] = req.start_date.isoformat()
    try:
        return preview_territories(p)
    except KeyError:
        raise HTTPException(404, "Город не загружен")


class BaseQuery(BaseModel):
    cities: Optional[list[str]] = None              # None — все загруженные города
    kinds: list[str] = ["lpu", "pharmacy"]
    lpu_ownership: list[str] = ["state", "private"]
    pharmacy_ownership: list[str] = ["state", "private"]
    categories: Optional[list[str]] = None
    lpu_care: CareFilter = "all"
    specialty: str = ""
    pharmacy_min_turnover: Optional[int] = None    # аптеки с оборотом юрлица на аптеку не ниже, руб./мес
    pharmacy_networks: Optional[list[str]] = None   # названия сетей; «Без сети» — одиночные аптеки
    only_target: bool = False                       # только ЛПУ, где есть врачи выбранной специальности
    scope: str = "city"                             # city | suburbs (город с пригородом) | region (область целиком)
    client_base: Optional[str] = None               # считать по базе заказчика (её города, её ЛПУ)
    regions: Optional[list[str]] = None             # для scope=region: какие области


@app.post("/api/base/summary")
def base_summary(q: BaseQuery):
    return base_export.summary(q.model_dump())


@app.get("/api/base/totals")
def base_totals():
    """ЛПУ, аптеки и врачи всей базы для главного экрана — мгновенно, из последней сводки."""
    return base_export.totals(BaseQuery(scope="region").model_dump())


class PointQuery(BaseModel):
    query: BaseQuery
    kind: str = "lpu"
    i: int = 0
    revision: str | None = None
    detail: bool = False


@app.post("/api/base/point")
def base_point(q: PointQuery):
    """Подпись точки на карте «Базы» (номер точки — как в /api/base/points с теми же фильтрами)."""
    r = base_export.point_info(q.query.model_dump(), q.kind, q.i, revision=q.revision, detail=q.detail)
    if r is None:
        raise HTTPException(404, "Точка не найдена — обновите карту")
    return r


@app.post("/api/base/points")
def base_points(q: BaseQuery):
    """Все ЛПУ и аптеки по фильтрам «Базы» — точками для карты России."""
    return base_export.points(q.model_dump())


class BaseExportQuery(BaseQuery):
    doctor_specialties: Optional[list[str]] = None


@app.post("/api/base/xlsx")
def base_xlsx(q: BaseExportQuery):
    data = base_export.workbook(q.model_dump())
    kinds = set(q.kinds or ["lpu", "pharmacy"])
    subject = "ЛПУ" if kinds == {"lpu"} else "аптек" if kinds == {"pharmacy"} else "ЛПУ_и_аптек"
    if q.scope == "region":
        regions = q.regions or sorted({e["region"] for slug in q.cities or [] if (e := C.entry(slug)) and e.get("region")})
        area = regions[0] if len(regions) == 1 else f"{len(regions) or 'все'}_регионов"
    elif len(q.cities or []) == 1:
        e = C.entry(q.cities[0])
        area = (e or {}).get("name", q.cities[0])
    else:
        area = f"{len(q.cities or []) or 'все'}_городов"
    fname = f"База_{subject}_{area}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@app.post("/api/plan/xlsx")
def plan_xlsx(req: PlanRequest):
    result = _plan(req)
    s = result["summary"]
    fname = f"План_{s['city']}_{s['specialty']}_{s['start']}.xlsx"
    return Response(build_workbook(result),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


def _file(data: bytes, fname: str, media: str) -> Response:
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.post("/api/market-analysis/{mode}")
def market_analysis(mode: Literal["comparison", "distribution"], file: UploadFile = File(...), excel: bool = False):
    from . import market_analysis as analysis
    try:
        content = file.file.read()
        if mode == "comparison":
            result = analysis.compare(content)
            if excel:
                return _file(analysis.compare_xlsx(result), "Сверка_адресов.xlsx", XLSX)
        else:
            result = analysis.distribution(content)
            if excel:
                return _file(analysis.distribution_xlsx(result), "Нумерическая_дистрибуция.xlsx", XLSX)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class PdfRequest(PlanRequest):
    client: str = ""


@app.post("/api/plan/pdf")
def plan_pdf(req: PdfRequest):
    result = _plan(req)
    s = result["summary"]
    return _file(report_pdf.plan_pdf(result, req.client), f"Отчёт_{s['city']}_{s['specialty']}_{s['start']}.pdf",
                 "application/pdf")


# ─── Обратный расчёт ─────────────────────────────────────────────────────────

class GoalRequest(PlanRequest):
    target_doctors: Optional[int] = Field(None, ge=1)
    auto_d: bool = False   # визиты в день не задаёт пользователь — подбирает инструмент
    target_pharmacies: Optional[int] = Field(None, ge=1)
    use_doctors: bool = True       # цель охвата: врачи, аптеки или и то и другое
    use_pharmacies: bool = False


@app.post("/api/goal")
def goal(req: GoalRequest):
    """Цель «охватить T врачей с кратностью f за срок» → сколько медпредов и визитов в день."""
    body = {**req.model_dump(exclude={"target_doctors", "auto_d", "target_pharmacies", "use_doctors", "use_pharmacies"}),
            "territories": None}
    if req.auto_d:
        if not (req.use_doctors or req.use_pharmacies):
            raise HTTPException(400, "Отметьте, кого охватить: врачей, аптеки или и тех и других.")
        # пробный план только для калибровки длины дня и подсчёта доступных врачей и аптек
        body["doctor_visits_per_day"] = 6
        body["pharmacy_visits_per_day"] = 3
        probe = _plan(PlanRequest(**body))
        return goal_team(probe, req.target_doctors, req.target_pharmacies, req.use_doctors, req.use_pharmacies)
    probe = _plan(PlanRequest(**body))
    return goal_options(probe, req.target_doctors)


# ─── Проекты ─────────────────────────────────────────────────────────────────

class ProjectBody(BaseModel):
    status: Optional[str] = None
    name: Optional[str] = None
    client: Optional[str] = None
    conditions: Optional[dict] = None
    goal: Optional[dict] = None
    cities: Optional[list[dict]] = None


def _project(pid: str) -> dict:
    try:
        return P.get(pid)
    except KeyError:
        raise HTTPException(404, "Проект не найден")


@app.get("/api/projects")
def projects_list():
    return P.list_projects()


@app.post("/api/projects")
def projects_create(body: ProjectBody):
    return P.create(body.model_dump(exclude_none=True))


@app.get("/api/projects/{pid}")
def projects_get(pid: str):
    return {**_project(pid), "job": P.job_status(pid)}


@app.put("/api/projects/{pid}")
def projects_update(pid: str, body: ProjectBody):
    _project(pid)
    return P.update(pid, body.model_dump(exclude_none=True))


@app.delete("/api/projects/{pid}")
def projects_delete(pid: str):
    try:
        P.delete(pid)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/projects/{pid}/compute")
def projects_compute(pid: str):
    _project(pid)
    try:
        return P.start(pid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}/plans/{slug}")
def projects_get_plan(pid: str, slug: str, revision: str | None = None):
    pr = _computed(pid, revision)
    if slug not in {row["slug"] for row in (pr.get("result") or {}).get("cities", [])}:
        raise HTTPException(404, "План города не рассчитан.")
    plan = P.load_plan(pid, slug, pr["result"]["id"])
    if plan is None:
        raise HTTPException(404, "План города не рассчитан.")
    return plan_results.register(plan)


@app.put("/api/projects/{pid}/plans/{slug}")
def projects_save_plan(pid: str, slug: str, req: PlanRequest, recompute: bool = False, revision: str | None = None):
    """Сохранить план города (параметры, правки базы, границы зон) в проект.
    recompute — сразу пересчитать проект в фоне."""
    _project(pid)
    try:
        pr = P.save_city_plan(pid, slug, req.model_dump(mode="json"), revision)
        if recompute:
            pr["job"] = P.start(pid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return pr


@app.get("/api/projects/{pid}/job")
def projects_job(pid: str):
    return P.job_status(pid)


def _computed(pid: str, revision: str | None = None) -> dict:
    pr = _project(pid)
    result = pr.get("result")
    if not result or not result.get("id"):
        raise HTTPException(400, "Сначала рассчитайте проект.")
    if revision is not None and revision != result["id"]:
        result = P.load_result(pid, revision)
        if result is None:
            raise HTTPException(404, "Версия расчёта не найдена.")
    if result["stale"] and revision is None:
        raise HTTPException(409, "Условия изменились. Пересчитайте проект или явно откройте предыдущую версию.")
    return {**pr, **result["inputs"], "result": result}


@app.get("/api/projects/{pid}/xlsx")
def projects_xlsx(pid: str, revision: str | None = None):
    pr = _computed(pid, revision)
    return _file(project_xlsx.build(pr), f"Проект_{pr['name']}.xlsx", XLSX)


@app.get("/api/projects/{pid}/pdf")
def projects_pdf(pid: str, revision: str | None = None):
    pr = _computed(pid, revision)
    return _file(report_pdf.project_pdf(pr), f"Отчёт_{pr['name']}.pdf", "application/pdf")


# ─── Данные визитов: дополнение базы и сверка числа врачей ───────────────────

@app.get("/api/field-data")
def field_data_all():
    return FD.list_all()


@app.post("/api/field-data")
async def field_data_import(title: str = Form(...), files: list[UploadFile] = File(...)):
    data = [(f.filename or "visits.xlsx", await f.read()) for f in files]
    return FD.start_import(title.strip() or "Данные визитов", data)


@app.get("/api/field-data/{key}/status")
def field_data_status(key: str):
    return FD.status(key)


@app.get("/api/field-data/{key}/xlsx")
def field_data_xlsx(key: str):
    try:
        d = FD.get(key)
    except KeyError:
        raise HTTPException(404, "Загрузка не найдена")
    return _file(FD.comparison_xlsx(key), f"Сверка_врачей_{d['title']}.xlsx", XLSX)


@app.delete("/api/field-data/{key}")
def field_data_delete(key: str):
    FD.delete(key)
    return {"ok": True}


# ─── Собранный фронтенд ──────────────────────────────────────────────────────
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):  # неизвестный адрес API — ошибка, а не страница интерфейса
            raise HTTPException(404, "Сервер не знает этого запроса — перезапустите программу (run.bat).")
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
