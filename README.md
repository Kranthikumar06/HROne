# HROne Attendance API

A FastAPI and MongoDB service for employee attendance tracking and reporting.

The API supports employee management, punch-in/punch-out workflows, attendance corrections,
and monthly and department-level analytics. The implementation follows the contract in
[`openapi.yaml`](openapi.yaml).

## Features

- Employee creation and paginated listing.
- Attendance punch-in and punch-out.
- Support for `PRESENT`, `WFH`, and `ON_DUTY` attendance.
- IST-aware attendance dates, including overnight shifts.
- Late minutes, overtime, work hours, and half-day calculations.
- Attendance filtering and pagination.
- Manual regularization with an append-only audit history.
- Employee monthly summaries.
- Department summaries and daily trends.
- Late-arrival leaderboard with standard competition ranking.
- MongoDB explain plans for the main read operations.
- MongoDB indexes and atomic updates for duplicate and concurrent requests.

## Project structure

```text
app/main.py                    FastAPI application
sample_seed.py                 Loads sample data
sample_data/                   Sample employees and attendance records
openapi.yaml                   API contract and business rules
DATA_MODEL.md                  MongoDB document model
REVIEW.md                      Review of the original implementation
DECISIONS.md                   Design and scaling decisions
requirements.txt               Python dependencies
```

## Technology

- Python 3.11+
- FastAPI
- Uvicorn
- PyMongo
- MongoDB 6+
- python-dotenv

## Configuration

The application reads `MONGO_URI` and `MONGO_DB` from the environment. For local development,
create a `.env` file in the project root:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone
```

MongoDB Atlas can be used instead of a local MongoDB server. Do not commit `.env` or expose an
Atlas connection string because it may contain a password. The root `.gitignore` excludes it.

## Run locally on Windows

From the project root:

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If the virtual environment is already present, activation is optional:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start MongoDB with Docker:

```powershell
docker run -d -p 27017:27017 --name mongo mongo:7
```

If the container already exists:

```powershell
docker start mongo
```

Load the sample data:

```powershell
.\venv\Scripts\python.exe sample_seed.py
```

Start the API:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

The API will be available at:

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Health check: <http://127.0.0.1:8000/health>

## API overview

| Area | Endpoints |
|---|---|
| Employees | `POST /employees`, `GET /employees` |
| Attendance | `POST /attendance/punch-in`, `POST /attendance/punch-out`, `GET /attendance` |
| Corrections | `PATCH /attendance/{emp_code}/{date}` |
| Employee analytics | `GET /analytics/employees/{emp_code}/monthly` |
| Department analytics | `GET /analytics/departments/summary` |
| Trends | `GET /analytics/departments/{department}/trend` |
| Leaderboard | `GET /analytics/leaderboard/late` |
| Query diagnostics | `GET /admin/explain/{endpoint}` |

Use Swagger UI for interactive requests or import the endpoints into Postman.

## Implementation notes

### Time handling

The API accepts timestamps as epoch milliseconds and stores them as BSON UTC datetimes.
Calendar dates and shift times are represented as strings in IST (`UTC+05:30`).
Incoming timestamps are truncated to whole seconds before storage.

For an overnight shift such as `22:00` to `06:00`, a punch-in after midnight belongs
to the previous shift-start date.

### Derived attendance values

The API calculates and stores:

- `late_minutes`: minutes after the strict ten-minute grace period.
- `overtime_minutes`: overtime only when it reaches 30 minutes.
- `work_hours`: duration rounded half-up to two decimal places.
- `half_day`: true when rounded work hours are below 4.50.

Analytics use these stored values. They do not recalculate them from punch times.

### Reliability and consistency

- Unique indexes prevent duplicate employee codes and duplicate attendance dates.
- Punch-out uses an atomic update so concurrent requests cannot both close a record.
- Regularization uses optimistic concurrency checks and appends one audit entry per successful correction.
- Analytics exclude attendance before an employee's `joined_on` date.
- Employees with no logs still contribute to department headcount when eligible for the period.

## Verification

After starting the API, open <http://127.0.0.1:8000/docs> and verify:

1. `GET /health`
2. `GET /employees`
3. Create an employee.
4. Punch in and punch out using a new attendance date.
5. List the attendance record.
6. Run the employee monthly and department analytics endpoints.
7. Run the trend, leaderboard, and explain endpoints.

Use a new employee code and attendance date for repeatable tests. A second punch-in for the
same employee and calculated attendance date should return `409 Conflict`.

## Supporting documentation

- [`openapi.yaml`](openapi.yaml) - complete paths, request/response schemas, and rules.
- [`DATA_MODEL.md`](DATA_MODEL.md) - MongoDB document shapes and sample data.
- [`REVIEW.md`](REVIEW.md) - problems identified and fixes made.
- [`DECISIONS.md`](DECISIONS.md) - indexing, concurrency, ranking, headcount, and scaling decisions.
