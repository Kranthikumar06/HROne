# Candidate kit

## Interview walkthrough

This implementation keeps the API contract in `openapi.yaml`, the MongoDB document shape in
`DATA_MODEL.md`, and the application in `app/main.py`.

### Architecture in one minute

- FastAPI validates request bodies and query parameters with Pydantic.
- MongoDB stores `employees` and `attendance_logs`.
- The API converts epoch milliseconds at the HTTP boundary to BSON datetimes internally.
- Calendar dates and shift times stay as strings because attendance dates are IST business values.
- Derived attendance fields are recomputed by the API and analytics read the stored derived values.
- Startup creates idempotent indexes for uniqueness, filtering, sorting, and headcount queries.

### Important design decisions to explain

1. The unique `(emp_code, date)` index makes concurrent punch-ins safe: one insert succeeds and the other becomes
   a `409` duplicate-key response.
2. Punch-out uses a compare-and-set update on `punch_out: null`, so simultaneous punch-outs cannot both close a record.
3. Regularization compares the original status, punch times, and history in the update filter. A concurrent edit therefore
   returns `409` instead of silently losing another audit entry.
4. Overnight shifts use the IST punch-in time and assign after-midnight punches to the previous shift-start date.
5. Decimal `ROUND_HALF_UP` is used because Python's binary float rounding is not the assignment's rounding rule.
6. Analytics use MongoDB grouping for attendance metrics and fill missing trend dates in the response range.

### Demo flow in Swagger/Postman

1. `GET /health`
2. `GET /employees`
3. `POST /employees`
4. `POST /attendance/punch-in`
5. `GET /attendance`
6. `POST /attendance/punch-out`
7. `PATCH /attendance/{emp_code}/{date}`
8. Try the four `/analytics` endpoints.
9. Try `/admin/explain/attendance_list` and look for an indexed plan.

### Local verification

```powershell
.\venv\Scripts\python.exe sample_seed.py
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) and use **Try it out**.

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.
