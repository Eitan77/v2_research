import duckdb

con = duckdb.connect(r"D:\AlgoResearch\data\catalog.duckdb", read_only=True)
print(con.execute("SELECT sql FROM duckdb_views() WHERE view_name='bars_1m'").fetchone()[0])
print(con.execute("SELECT date,session_open,session_close FROM calendar WHERE TRY_CAST(date AS DATE) IN (DATE '2025-07-03',DATE '2025-11-28',DATE '2025-12-24')").df().to_string(index=False))
print(con.execute("SELECT date,session_open,session_close FROM calendar WHERE TRY_CAST(date AS DATE) IN (DATE '2025-07-02',DATE '2025-11-26') LIMIT 10").df().to_string(index=False))
print(con.execute("SELECT date,open,close,session_open,session_close FROM calendar WHERE TRY_CAST(date AS DATE) IN (DATE '2025-07-02',DATE '2025-11-26') LIMIT 4").df().to_string(index=False))
print(con.execute("SELECT date,open,close FROM calendar WHERE TRY_CAST(date AS DATE) IN (DATE '2025-07-03',DATE '2025-11-28',DATE '2025-12-24') LIMIT 6").df().to_string(index=False))
