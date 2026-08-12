import duckdb
c=duckdb.connect(r'D:\AlgoResearch\data\catalog.duckdb',read_only=True)
print(c.execute("select table_name from information_schema.tables where table_name ilike '%bar%' order by 1").fetchall())
print(c.execute("describe bars_1m").fetchdf().to_string(index=False))
