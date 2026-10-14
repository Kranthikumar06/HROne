# Decisions

1. **Indexes.** I created a unique employee-code index to enforce the business identifier, a unique
`(emp_code, date)` attendance index to enforce one record per day and make punch-in races safe, a date/employee
index for sorted attendance pages, an employee/date/status index for filtered reads, and a department/joined-date
index for headcount queries. I considered a separate index on every derived analytics field, but rejected it because
the analytics are period- and employee-scoped and the extra indexes would add write cost without helping the main
filters.

2. **Punch-in race.** Both requests may read no existing record, but both then attempt the unique
`(emp_code, date)` insert. MongoDB accepts exactly one insert; the other receives `DuplicateKeyError`, which the API
translates to 409. The application does not rely on a check-then-insert race-prone guarantee.

3. **Ties.** Rows are sorted by total late minutes descending and employee code ascending. The rank is the position
of the first employee with that total, so equal totals share a rank and later ranks are skipped. The limit is applied
to the rank, not the number of returned rows, so all employees tied at the cutoff are returned.

4. **Headcount.** Department summaries begin with employees whose `joined_on` is on or before the month end, then
group those employees by department before reading attendance logs. Therefore an employee with zero logs still
contributes to headcount.

5. **One change for 100x the data.** I would precompute daily/monthly rollups asynchronously so analytics could read
small summary collections instead of aggregating raw attendance logs on every request.
