# Employee Attendance & Analytics API

FastAPI and MongoDB service for employee attendance tracking, attendance corrections, and
department-level analytics. This repository is the completed take-home assignment implementation.

## What is included

- `app/main.py` - FastAPI application and all API logic.
- `openapi.yaml` - authoritative API contract and business rules R1-R10.
- `DATA_MODEL.md` - MongoDB collections, field types, and sample document semantics.
- `sample_seed.py` - loads the supplied sample employees and attendance records.
- `sample_data/` - seed data in MongoDB Extended JSON format.
- `REVIEW.md` - review of the starter implementation and completed fixes.
- `DECISIONS.md` - important design and scaling decisions.
- `requirements.txt` - pinned Python dependencies.

## Architecture

The application has two MongoDB collections:

- `employees` - employee identity, department, shift, joining date, and server-generated creation time.
- `attendance_logs` - one attendance record per `(emp_code, date)`, including punch times, derived values, and
  manual-correction history.

FastAPI validates request data with Pydantic. The application converts epoch milliseconds at the HTTP boundary
to BSON datetimes for MongoDB, then converts BSON datetimes back to epoch milliseconds in responses. Calendar
dates and shift times remain strings because they represent IST business values rather than instants.

MongoDB indexes are created idempotently at startup:

- Unique employee-code index.
- Unique attendance `(emp_code, date)` index.
- Date and employee index for sorted attendance listing.
- Employee/date/status index for filtered attendance reads.
- Department/joined-date index for headcount queries.

## Requirements

- Windows PowerShell, macOS/Linux shell, or equivalent terminal.
- Python 3.11 or newer.
- MongoDB 6.0 or newer. MongoDB 7 is used by the assignment.
- Git for version control.

MongoDB can run locally with Docker or remotely through MongoDB Atlas. Atlas is suitable for the free tier.

## Configuration

The application reads:

```text
MONGO_URI
MONGO_DB
```

from the environment. `python-dotenv` loads a local `.env` file during development, while real environment
variables take precedence. The repository intentionally does not contain a `.env` file.

Create `D:\HROne\candidate_kit\.env` locally with values similar to:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone
```

For MongoDB Atlas, put the Atlas connection string in `MONGO_URI`. Never commit that file or expose a
connection string containing a password. The root [.gitignore](.gitignore) excludes `.env`, `venv/`, Python
cache directories, and compiled Python files.

## Windows setup

Run these commands from the repository root:

```powershell
cd D:\HROne\candidate_kit
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell prevents activation, run the project using the virtual-environment executable directly:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start MongoDB

### Option A: Docker

```powershell
docker run -d -p 27017:27017 --name mongo mongo:7
```

If the container already exists, start it with:

```powershell
docker start mongo
```

Confirm it is running:

```powershell
docker ps
```

### Option B: MongoDB Atlas

Create a free Atlas cluster, create a database user, allow the development machine's IP address, and place
the connection string in the local `.env`. Do not commit the URI because it contains credentials.

## Seed sample data

The seed script loads the supplied six employees and nine attendance records:

```powershell
.\venv\Scripts\python.exe sample_seed.py
```

The script uses the same `MONGO_URI` and `MONGO_DB` settings as the API. Run it against a disposable
database when resetting a test environment.

## Run the API

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

The service is available at:

- Health check: <http://127.0.0.1:8000/health>
- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

`GET /health` returns `200` only when MongoDB responds to a ping. If MongoDB is unavailable, it returns `503`.

## API endpoints

### System and employees

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | MongoDB readiness check |
| `POST` | `/employees` | Create an employee |
| `GET` | `/employees` | List employees with department filtering and pagination |

### Attendance

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/attendance/punch-in` | Create a daily attendance record |
| `POST` | `/attendance/punch-out` | Atomically close the latest open record |
| `GET` | `/attendance` | Filtered, sorted, paginated attendance listing |
| `PATCH` | `/attendance/{emp_code}/{date}` | Correct a record and append an audit entry |

### Analytics

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/analytics/employees/{emp_code}/monthly` | Employee monthly attendance summary |
| `GET` | `/analytics/departments/summary` | Department monthly summary |
| `GET` | `/analytics/leaderboard/late` | Late-arrival leaderboard with competition ranking |
| `GET` | `/analytics/departments/{department}/trend` | Daily department trend with gap-filled dates |

### Diagnostics

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/explain/{endpoint}` | MongoDB `executionStats` explain output |

The full parameter and response contract is in [openapi.yaml](openapi.yaml).

## Important behavior

### Time and dates

- API timestamps are integer epoch milliseconds, not date strings.
- MongoDB stores timestamps as BSON UTC datetimes.
- Calendar dates are `YYYY-MM-DD`.
- Shift times are `HH:MM` in IST (`UTC+05:30`).
- Incoming instants are truncated to whole seconds before storage.
- Overnight shifts such as `22:00` to `06:00` assign after-midnight punches to the previous shift-start date.

Example conversion in PowerShell:

```powershell
[DateTimeOffset]::Parse('2026-07-15T09:30:00+05:30').ToUnixTimeMilliseconds()
```

### Derived attendance fields

The API computes and stores:

- `late_minutes` - strictly after the ten-minute grace period, using whole elapsed minutes.
- `overtime_minutes` - counted only when overtime reaches at least 30 minutes.
- `work_hours` - half-up rounded to two decimal places.
- `half_day` - `true` when rounded work hours are below `4.50`.

Analytics read these stored derived values rather than recomputing them from punch times.

### Concurrency and audit history

- The unique `(emp_code, date)` index makes duplicate punch-ins return `409`.
- Punch-out uses an atomic `punch_out: null` update, so simultaneous punch-outs cannot both succeed.
- Regularization uses optimistic concurrency matching and returns `409` if another correction changed the record.
- Punch-in and punch-out do not write audit history.
- Every successful regularization appends one history entry containing only fields that changed.

### Analytics rules

- `PRESENT`, `WFH`, and `ON_DUTY` count as present.
- `ABSENT` and `LEAVE` do not count as present.
- Present half-days count as `0.5`.
- Working days are Monday through Friday.
- Attendance before an employee's `joined_on` date is excluded from employment-period analytics.
- Employees who joined by the relevant period still count in headcount even with zero attendance logs.
- Trend responses include every requested calendar day, including weekends and days without records.
- Late leaderboard ties use standard competition ranking (`1, 2, 2, 4`).

## Suggested Postman or Swagger test flow

1. `GET /health`
2. `GET /employees`
3. `POST /employees`
4. `POST /attendance/punch-in`
5. `GET /attendance`
6. `POST /attendance/punch-out`
7. `PATCH /attendance/{emp_code}/{date}`
8. `GET /analytics/employees/{emp_code}/monthly?month=2026-07`
9. `GET /analytics/departments/summary?month=2026-07`
10. `GET /analytics/leaderboard/late?month=2026-07`
11. `GET /analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07`
12. `GET /admin/explain/attendance_list?page=1&page_size=20`

For repeatable tests, use a new employee code and a new attendance date. A second punch-in for the same
employee and calculated attendance date should correctly return `409`.

## Submission checklist

Before pushing:

```powershell
git status
git diff --cached --name-only
git ls-files .env
```

Confirm that:

- `.env` is not tracked.
- `venv/` and Python cache files are not tracked.
- `requirements.txt` is present.
- `app/main.py`, `openapi.yaml`, `DATA_MODEL.md`, `REVIEW.md`, and `DECISIONS.md` are present.
- The API starts with the documented Uvicorn command.
- `/health` and `/docs` work against the configured MongoDB instance.

Do not commit credentials, Atlas connection strings, local virtual environments, or temporary database exports.
