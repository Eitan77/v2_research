from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
roles = pd.read_parquet(ROOT / "campaigns/CAM-0600/artifacts/shared/split_target_change_roles_0930.parquet")
roles["target_ts"] = pd.to_datetime(roles.target_ts, utc=True)
row = roles.iloc[0]
with duckdb.connect(r"D:\AlgoResearch\data\raw\alpaca\market\stocks\quotes_sip\schema_v1\quote_lake.duckdb", read_only=True) as con:
    print(row[["symbol", "target_ts", "role"]].to_dict())
    print(con.execute("describe sip_quotes").fetchall())
    print(con.execute("select count(*), min(quote_ts), max(quote_ts) from sip_quotes where symbol=?", [row.symbol]).fetchall())
    print(con.execute("select quote_ts,bid_price,ask_price from sip_quotes where symbol=? and quote_ts between ? and ? order by quote_ts limit 5", [row.symbol, row.target_ts.to_pydatetime(), (row.target_ts + pd.Timedelta(minutes=10)).to_pydatetime()]).fetchall())
