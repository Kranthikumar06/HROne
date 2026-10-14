# Review of the starter

| # | Where | What was wrong | How you'd notice it | How it was fixed |
|---|---|---|---|---|
| 1 | `health` | Always returned 200 without checking MongoDB. | Stop MongoDB and call `/health`. | Added a MongoDB `ping` and return 503 on failure. |
| 2 | Employee creation/listing | No database-enforced uniqueness, wrong response time format, and list total ignored filters. | Concurrent duplicate POSTs; inspect `created_at`; filter by department. | Added a unique index, duplicate-key handling, epoch serialization, filtered totals, sorting, and pagination. |
| 3 | Pagination | Employee pagination skipped the first page because it used `page * page_size`. | Request `page=1`. | Changed the offset to `(page - 1) * page_size` and added bounds. |
| 4 | Time handling | Used local/naive time, seconds-looking input could be accepted, and milliseconds were not truncated. | Use an epoch value with milliseconds or test UTC/IST boundaries. | Added epoch validation, UTC storage, IST calendar conversion, and whole-second truncation. |
| 5 | Punch-in | Did not validate the employee/status, did not support overnight attendance dates, and was vulnerable to duplicate races. | Unknown employee, invalid status, overnight punch, or concurrent requests. | Added validation, overnight date calculation, and a unique natural-key index with duplicate-key handling. |
| 6 | Punch-out | Missing endpoint and atomic close operation. | Call the documented endpoint or send two simultaneous punch-outs. | Added most-recent-open-record lookup, validation, derived fields, and compare-and-set update. |
| 7 | Attendance listing | Loaded the entire collection into memory, omitted deterministic secondary sorting, and returned Mongo-specific values. | Use a large collection or inspect `_id`/datetime values. | Added indexed query pagination, sorting, validation, and API serialization. |
| 8 | Regularization | Missing endpoint, no audit history, no validation, and no derived-field recomputation. | Correct a record and inspect history/derived values. | Added validation, recomputation, change-only history entries, and atomic updates. |
| 9 | Analytics | All required analytics endpoints were missing. | Call any `/analytics` endpoint. | Added monthly, department, leaderboard, and trend responses with the documented rules. |
| 10 | Explain endpoint | Missing endpoint and explain plans. | Call `/admin/explain/...`. | Added endpoint-specific explain support and required-parameter checks. |

The starter's use of MongoDB and FastAPI was appropriate; those choices were retained. The seeded document shape and
natural key (`emp_code`, `date`) were also retained.
