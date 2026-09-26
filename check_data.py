"""일봉이 잘 들어있는지 빠르게 확인하는 스크립트. (DB가 파일 여러 개로 나뉘어 있어도 MarketDataStore가 알아서 처리)"""
import sys
sys.path.insert(0, "src")

from config_loader import load_config
from data_layer.storage import MarketDataStore

store = MarketDataStore(load_config()["data"]["db_path"], readonly=True)
with store._connect() as conn:
    import pandas as pd
    print(pd.read_sql("SELECT * FROM ohlcv ORDER BY date DESC LIMIT 5", conn))
