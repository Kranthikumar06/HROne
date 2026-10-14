"""Employee Attendance & Analytics API.

The API boundary uses epoch milliseconds; MongoDB stores instants as BSON dates.
Calendar dates and shift times remain strings because they are IST business values.
"""
import calendar
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Optional

from bson import ObjectId
from bson.timestamp import Timestamp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}
MAX_EPOCH = 4102444800000

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def parse_date(value: str) -> date:
    """Parse the API's strict YYYY-MM-DD calendar-date representation."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc


def parse_month(value: str) -> tuple[date, date]:
    """Return the first and last calendar dates for a YYYY-MM month."""
    if len(value) != 7 or value[4] != "-":
        raise ValueError("month must be YYYY-MM")
    try:
        year, month = map(int, value.split("-"))
        first = date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError("month must be YYYY-MM") from exc
    return first, date(year, month, calendar.monthrange(year, month)[1])


def validate_epoch(value: Optional[int]) -> Optional[int]:
    """Reject seconds, floats, strings, and timestamps outside the contract range."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100000000000 <= value <= MAX_EPOCH:
        raise ValueError("must be epoch milliseconds")
    return value


def ms_to_dt(value: int) -> datetime:
    """Convert API milliseconds to a naive UTC datetime for PyMongo."""
    return datetime.fromtimestamp(value / 1000, UTC).replace(tzinfo=None, microsecond=0)


def dt_to_ms(value: Optional[datetime]) -> Optional[int]:
    """Convert a MongoDB datetime back to the API's epoch-millisecond format."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def local_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def shift_datetime(day: date, shift: str, overnight: bool = False) -> datetime:
    h, m = map(int, shift.split(":"))
    base = datetime.combine(day, time(h, m), IST)
    return (base + timedelta(days=1)) if overnight else base


def attendance_day(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    """Resolve the IST attendance date, including the previous-day side of an overnight shift."""
    local = local_dt(punch_in)
    start_minutes = int(shift_start[:2]) * 60 + int(shift_start[3:])
    end_minutes = int(shift_end[:2]) * 60 + int(shift_end[3:])
    day = local.date()
    if end_minutes <= start_minutes and local.hour * 60 + local.minute < end_minutes:
        day -= timedelta(days=1)
    return day.isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str) -> int:
    """Apply the strict ten-minute grace period, then floor elapsed minutes."""
    local = local_dt(punch_in)
    shift_day = parse_date(attendance_day(punch_in, shift_start, shift_end))
    start = shift_datetime(shift_day, shift_start)
    elapsed_seconds = (local - start).total_seconds()
    return int(elapsed_seconds // 60) if elapsed_seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """Calculate hours using decimal half-up rounding required by R4."""
    value = (Decimal(str((punch_out - punch_in).total_seconds())) / Decimal("3600")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(value)


def compute_overtime(punch_in: datetime, punch_out: datetime, shift_start: str, shift_end: str) -> int:
    """Count overtime only when the whole-minute duration reaches 30 minutes."""
    start_minutes = int(shift_start[:2]) * 60 + int(shift_start[3:])
    end_minutes = int(shift_end[:2]) * 60 + int(shift_end[3:])
    shift_day = parse_date(attendance_day(punch_in, shift_start, shift_end))
    end = shift_datetime(shift_day, shift_end, end_minutes <= start_minutes)
    minutes = int((punch_out.replace(tzinfo=UTC) - end.astimezone(UTC)).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def calculate_derived(status: str, punch_in: Optional[datetime], punch_out: Optional[datetime], employee: dict[str, Any]) -> dict[str, Any]:
    """Compute all read-only attendance fields from the final attendance state."""
    if status not in PRESENCE or punch_in is None:
        return {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    late = compute_late_minutes(punch_in, employee["shift_start"], employee["shift_end"])
    if punch_out is None:
        return {"work_hours": None, "late_minutes": late, "overtime_minutes": 0, "half_day": False}
    hours = compute_work_hours(punch_in, punch_out)
    return {
        "work_hours": hours,
        "late_minutes": late,
        "overtime_minutes": compute_overtime(punch_in, punch_out, employee["shift_start"], employee["shift_end"]),
        "half_day": hours < 4.50,
    }


def serialize_value(value: Any) -> Any:
    """Convert BSON-only values into JSON-safe API values."""
    if isinstance(value, datetime):
        return dt_to_ms(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Timestamp):
        return {"time": value.time, "increment": value.inc}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    return value


def serialize_record(doc: dict[str, Any]) -> dict[str, Any]:
    doc = {key: value for key, value in doc.items() if key != "_id"}
    doc.setdefault("punch_in", None)
    doc.setdefault("punch_out", None)
    doc.setdefault("work_hours", None)
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    doc.setdefault("half_day", False)
    doc.setdefault("history", [])
    return serialize_value(doc)


def working_days(first: date, last: date) -> int:
    if last < first:
        return 0
    return sum((first + timedelta(days=i)).weekday() < 5 for i in range((last - first).days + 1))


def month_filter(month: str) -> tuple[date, date, dict[str, Any]]:
    try:
        first, last = parse_month(month)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return first, last, {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}


class BaseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EmployeeIn(BaseRequest):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value: str) -> str:
        parse_date(value)
        return value

    @model_validator(mode="after")
    def different_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseRequest):
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_punched_at(cls, value: Optional[int]) -> Optional[int]:
        return validate_epoch(value)


class PunchOutIn(BaseRequest):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at")
    @classmethod
    def valid_punched_at(cls, value: Optional[int]) -> Optional[int]:
        return validate_epoch(value)


class RegularizeIn(BaseRequest):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_times(cls, value: Optional[int]) -> Optional[int]:
        return validate_epoch(value)


@app.on_event("startup")
def create_indexes():
    """Create idempotent indexes for uniqueness, filtering, sorting, and headcount."""
    db.employees.create_index("emp_code", unique=True, name="ux_employee_code")
    db.employees.create_index([("department", 1), ("joined_on", 1)], name="ix_employee_department_joined")
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True, name="ux_attendance_emp_date")
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)], name="ix_attendance_date_emp")
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1), ("status", 1)], name="ix_attendance_filters")


@app.get("/health")
def health():
    """Readiness probe: return 503 instead of a false healthy response if MongoDB is down."""
    try:
        client.admin.command("ping")
    except Exception as exc:
        raise HTTPException(503, "MongoDB unavailable") from exc
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    """Create an employee; the unique index handles concurrent duplicate requests."""
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "emp_code already exists") from exc
    response = {key: doc[key] for key in body.model_fields}
    response["created_at"] = doc["created_at"]
    return serialize_value(response)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = {"department": department} if department is not None else {}
    total = db.employees.count_documents(query)
    docs = db.employees.find(query, {"_id": 0}).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    return {"items": [serialize_value(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    """Create one open attendance record; the natural-key index prevents duplicates."""
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    instant = ms_to_dt(body.punched_at if body.punched_at is not None else now_ms())
    record_date = attendance_day(instant, employee["shift_start"], employee["shift_end"])
    doc = {
        "emp_code": body.emp_code, "date": record_date, "status": body.status,
        "punch_in": instant, "punch_out": None, "history": [],
    }
    doc.update(calculate_derived(body.status, instant, None, employee))
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(409, "already punched in for this date") from exc
    return serialize_record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    """Atomically close the latest eligible open record, including overnight shifts."""
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    out = ms_to_dt(body.punched_at if body.punched_at is not None else now_ms())
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$ne": None, "$lte": out}},
        sort=[("punch_in", -1)],
    )
    if not record:
        raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
    delta = (out - record["punch_in"]).total_seconds()
    if delta <= 0 or delta > 86400:
        raise HTTPException(422, "punch-out must be after punch-in and within 24 hours")
    derived = calculate_derived(record["status"], record["punch_in"], out, employee)
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {"punch_out": out, **derived}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return serialize_record(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
    status: Optional[str] = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    if date_from:
        parse_date(date_from)
    if date_to:
        parse_date(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not exceed date_to")
    if status is not None and status not in STATUSES:
        raise HTTPException(422, "invalid status")
    query: dict[str, Any] = {}
    if emp_code:
        query["emp_code"] = emp_code
    if status:
        query["status"] = status
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(query)
    docs = db.attendance_logs.find(query).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size)
    return {"items": [serialize_record(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    """Apply a manual correction and append exactly one change-only audit entry."""
    try:
        parse_date(date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "attendance record not found")
    new_status = body.status if body.status is not None else record.get("status")
    new_in = ms_to_dt(body.punch_in) if body.punch_in is not None else record.get("punch_in")
    new_out = ms_to_dt(body.punch_out) if body.punch_out is not None else record.get("punch_out")
    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT and LEAVE cannot have punch times")
        new_in = new_out = None
    elif new_in is None:
        raise HTTPException(422, "presence status requires punch_in")
    if new_in is not None and attendance_day(new_in, employee["shift_start"], employee["shift_end"]) != date:
        raise HTTPException(422, "punch_in does not belong to the record date")
    if new_out is not None:
        if new_in is None or (new_out - new_in).total_seconds() <= 0 or (new_out - new_in).total_seconds() > 86400:
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    derived = calculate_derived(new_status, new_in, new_out, employee)
    final = {"status": new_status, "punch_in": new_in, "punch_out": new_out, **derived}
    changes = {}
    for field in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"):
        before = record.get(field, [] if field == "history" else False if field == "half_day" else 0 if field in {"late_minutes", "overtime_minutes"} else None)
        after = final.get(field)
        if before != after:
            changes[field] = {"from": before, "to": after}
    if not changes:
        raise HTTPException(422, "request changes nothing")
    history = record.get("history", [])
    history_entry = {"at": ms_to_dt(now_ms()), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "status": record.get("status"),
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
            "history": record.get("history", []),
        },
        {"$set": final, "$push": {"history": history_entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "attendance record was changed concurrently")
    return serialize_record(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    """Aggregate one employee's stored attendance values for a calendar month."""
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "employee not found")
    first, last, log_query = month_filter(month)
    start = max(first, parse_date(employee["joined_on"]))
    log_query["date"]["$gte"] = max(first, parse_date(employee["joined_on"])).isoformat()
    logs = list(db.attendance_logs.aggregate([
        {"$match": {"emp_code": emp_code, **log_query}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}]},
                {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]}, 0
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }}
    ]))
    stats = logs[0] if logs else {}
    present = stats.get("present_days", 0)
    pct = None if working_days(start, last) == 0 else float((Decimal(str(present / working_days(start, last) * 100))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {"emp_code": emp_code, "month": month, "working_days": working_days(start, last), "present_days": present,
            "leave_days": stats.get("leave_days", 0), "late_count": stats.get("late_count", 0),
            "total_late_minutes": stats.get("total_late_minutes", 0),
            "total_overtime_minutes": stats.get("total_overtime_minutes", 0), "attendance_pct": pct}


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    """Aggregate department metrics while preserving employees with zero logs."""
    first, last, log_query = month_filter(month)
    employees = list(db.employees.find({"joined_on": {"$lte": last.isoformat()}, **({"department": department} if department else {})}, {"_id": 0}))
    groups: dict[str, list[dict[str, Any]]] = {}
    for employee in employees:
        groups.setdefault(employee["department"], []).append(employee)
    items = []
    for name in sorted(groups):
        codes = [employee["emp_code"] for employee in groups[name]]
        logs = list(db.attendance_logs.aggregate([
            {"$match": {"emp_code": {"$in": codes}, **log_query}},
            {"$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "_employee",
            }},
            {"$unwind": "$_employee"},
            {"$match": {"$expr": {"$gte": ["$date", "$_employee.joined_on"]}}},
            {"$group": {
                "_id": None,
                "present_days": {"$sum": {"$cond": [
                    {"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}]},
                    {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]}, 0
                ]}},
                "hours_total": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$ne": ["$work_hours", None]}]}, "$work_hours", 0]}},
                "hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$ne": ["$work_hours", None]}]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
            }}
        ]))
        stats = logs[0] if logs else {}
        items.append({"department": name, "headcount": len(groups[name]),
                      "present_days": stats.get("present_days", 0),
                      "avg_work_hours": None if not stats.get("hours_count") else float((Decimal(str(stats["hours_total"] / stats["hours_count"]))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                      "late_count": stats.get("late_count", 0),
                      "total_late_minutes": stats.get("total_late_minutes", 0),
                      "leave_count": stats.get("leave_count", 0),
                      "on_duty_count": stats.get("on_duty_count", 0)})
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    """Build a standard-competition ranking from grouped late-minute totals."""
    _, _, log_query = month_filter(month)
    employee_query = {"department": department} if department else {}
    employees = {item["emp_code"]: item for item in db.employees.find(employee_query, {"_id": 0})}
    totals = {
        item["_id"]: {"total": item["total"], "count": item["count"]}
        for item in db.attendance_logs.aggregate([
            {"$match": {"emp_code": {"$in": list(employees)}, **log_query}},
            {"$group": {
                "_id": "$emp_code",
                "total": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            }}
        ])
    }
    rows = sorted(((code, value) for code, value in totals.items() if value["total"] > 0), key=lambda pair: (-pair[1]["total"], pair[0]))
    items = []
    previous = None
    for position, (code, value) in enumerate(rows, 1):
        rank = position if previous != value["total"] else items[-1]["rank"]
        previous = value["total"]
        if rank > limit:
            break
        employee = employees[code]
        items.append({"rank": rank, "emp_code": code, "name": employee["name"], "department": employee["department"],
                      "total_late_minutes": value["total"], "late_count": value["count"]})
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(...)):
    """Return a gap-filled daily series and a seven-row moving attendance average."""
    try:
        first, last = parse_date(from_), parse_date(to)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if last < first or (last - first).days + 1 > 92:
        raise HTTPException(422, "invalid trend range")
    employees = list(db.employees.find({"department": department}, {"_id": 0}))
    if not employees:
        raise HTTPException(404, "department not found")
    codes = [item["emp_code"] for item in employees]
    daily_stats = db.attendance_logs.aggregate([
        {"$match": {"emp_code": {"$in": codes}, "date": {"$gte": from_, "$lte": to}}},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "_employee",
        }},
        {"$unwind": "$_employee"},
        {"$match": {"$expr": {"$gte": ["$date", "$_employee.joined_on"]}}},
        {"$group": {
            "_id": "$date",
            "present_count": {"$sum": {"$cond": [
                {"$in": ["$status", list(PRESENCE)]},
                {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                0,
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        }},
    ])
    by_day = {item["_id"]: item for item in daily_stats}
    items = []
    for index in range((last - first).days + 1):
        day = first + timedelta(days=index)
        key = day.isoformat()
        headcount = sum(parse_date(item["joined_on"]) <= day for item in employees)
        day_stats = by_day.get(key, {})
        present = day_stats.get("present_count", 0)
        rate = None if day.weekday() >= 5 or headcount == 0 else float((Decimal(str(present / headcount))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        values = [row["attendance_rate"] for row in items[-6:] if row["attendance_rate"] is not None]
        if rate is not None:
            values.append(rate)
        moving = None if not values else float((Decimal(str(sum(values) / len(values)))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        items.append({"date": key, "is_working_day": day.weekday() < 5, "headcount": headcount,
                      "present_count": present, "late_count": day_stats.get("late_count", 0),
                      "attendance_rate": rate, "moving_avg_7d": moving})
    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None,
                     department: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                     date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                     from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    """Expose executionStats for the same primary MongoDB read used by an endpoint."""
    allowed = {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}
    if endpoint not in allowed:
        raise HTTPException(422, "unknown endpoint")
    if endpoint == "attendance_list":
        query: dict[str, Any] = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        explain = db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        })
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": serialize_value(explain)}
    if endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from and to are required")
        parse_date(from_)
        parse_date(to)
        codes = [
            item["emp_code"]
            for item in db.employees.find({"department": department}, {"emp_code": 1, "_id": 0})
        ]
        if not codes:
            raise HTTPException(404, "department not found")
        pipeline: list[dict[str, Any]] = [
            {"$match": {"emp_code": {"$in": codes}, "date": {"$gte": from_, "$lte": to}}},
            {"$group": {"_id": "$date"}},
        ]
        collection = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        first, _, query = month_filter(month)
        employee = db.employees.find_one({"emp_code": emp_code}, {"joined_on": 1, "_id": 0})
        if not employee:
            raise HTTPException(404, "employee not found")
        query["date"]["$gte"] = max(first, parse_date(employee["joined_on"])).isoformat()
        pipeline = [
            {"$match": {"emp_code": emp_code, **query}},
            {"$group": {"_id": None, "late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}}},
        ]
        query["emp_code"] = emp_code
        collection = "attendance_logs"
    else:
        if not month:
            raise HTTPException(422, "month is required")
        _, _, query = month_filter(month)
        if department:
            codes = [
                item["emp_code"]
                for item in db.employees.find({"department": department}, {"emp_code": 1, "_id": 0})
            ]
            query["emp_code"] = {"$in": codes}
        pipeline = [
            {"$match": query},
            {"$group": {"_id": "$emp_code", "late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}}},
        ]
        collection = "attendance_logs"
    explain = db.command({
        "explain": {"aggregate": collection, "pipeline": pipeline, "cursor": {}},
        "verbosity": "executionStats",
    })
    return {"endpoint": endpoint, "collection": collection, "explain": serialize_value(explain)}
