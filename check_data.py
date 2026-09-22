import sqlite3
import pandas as pd

conn = sqlite3.connect("data/market_data.db")
df = pd.read_sql("SELECT * FROM ohlcv ORDER BY date DESC LIMIT 5", conn)
print(df)
