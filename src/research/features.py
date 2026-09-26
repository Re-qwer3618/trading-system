"""
전략 검증용 "특징(feature)"과 "결과(outcome)" 계산.

특징 = 신호가 난 날(t)의 종가까지의 정보만으로 계산되는 값 (거래량 축소/증가, 이동평균 이격,
RSI, 변동성 ...). 미래 데이터를 쓰지 않는 것이 가장 중요합니다 — 전부 rolling/shift(양수)만 씁니다.

결과 = 그 신호를 보고 t+1일 시가에 진입했을 때의 성과. 종가로 진입한다고 가정하면 "종가를 보고
종가에 산다"는 불가능한 가정이 되므로 다음날 시가를 씁니다 (백테스트 엔진의 종가 체결보다 보수적).
  ret_h      : t+1 시가 진입 -> t+h 종가 청산 수익률 (h=1이면 진입 당일 시가->종가)
  mae_h/mfe_h: 그 기간 중 최대 손실폭/최대 이익폭 (저가/고가 기준)
  adj_h      : 손절선(stop_pct)에 닿았으면 -stop_pct로 자른 수익률 (손절이 있는 실제 운용에 가깝게)
                일봉만으로는 같은 날 고가/저가 순서를 알 수 없어, 저가가 손절선 아래면 손절로 봅니다(보수적).
모든 수익률은 왕복 거래비용(cost)을 뺀 순수익률입니다.
"""

import numpy as np
import pandas as pd

HORIZONS = (1, 3, 5, 10, 20)

# 화면/보고서에 보여줄 한글 이름과 설명 (특징 이름 -> (이름, 설명))
FEATURE_INFO = {
    "vol_ratio_5_20": ("거래량 5일/20일", "최근 5일 평균 거래량 ÷ 20일 평균. 낮을수록 거래량 축소, 높을수록 증가"),
    "vol_ratio_1_20": ("당일 거래량/20일평균", "당일 거래량 ÷ 20일 평균. 급증 여부"),
    "vol_dryup_3_20": ("거래량 바닥(3일 최저/20일평균)", "최근 3일 중 가장 적은 거래량 ÷ 20일 평균. 낮을수록 바짝 마른 상태"),
    "vol_trend_5": ("거래량 추세(5일평균 변화)", "5일 평균 거래량이 5일 전 대비 얼마나 변했나"),
    "turnover_20": ("거래대금(20일, 로그)", "close x volume 20일 평균의 로그. 유동성/종목 크기"),
    "ret_1d": ("당일 등락률", "전일 종가 대비 당일 종가"),
    "ret_5d": ("5일 수익률", ""),
    "ret_20d": ("20일 수익률", ""),
    "ret_60d": ("60일 수익률", ""),
    "gap_open": ("시가 갭", "당일 시가 ÷ 전일 종가 - 1"),
    "body_pct": ("캔들 몸통", "(종가-시가) ÷ 시가. 양봉/음봉의 크기"),
    "upper_wick": ("윗꼬리", "고가 대비 몸통 위 꼬리 길이(%)"),
    "close_ma20_gap": ("20일선 이격", "종가 ÷ 20일 이동평균 - 1"),
    "ma5_ma20_gap": ("5-20일선 이격", "5일선 ÷ 20일선 - 1 (골든크로스 부근이면 0 근처)"),
    "ma20_ma60_gap": ("20-60일선 이격", "20일선 ÷ 60일선 - 1"),
    "ma20_slope_5": ("20일선 기울기", "20일선의 5일 변화율"),
    "rsi14": ("RSI(14)", ""),
    "atr14_pct": ("변동성 ATR(14)%", "평균 진폭 ÷ 종가"),
    "bb_width_20": ("볼린저 폭", "20일 표준편차 4배 ÷ 20일선. 낮을수록 횡보/수렴"),
    "range_5_pct": ("5일 등락폭", "최근 5일 (고가-저가) ÷ 종가"),
    "range_pos_60": ("60일 박스 내 위치", "60일 저가~고가 사이 어디쯤인가 (0=바닥, 1=천장)"),
    "dist_high_250": ("250일 고점 대비", "종가 ÷ 250일 고가 - 1 (0에 가까울수록 신고가 부근)"),
}
FEATURES = list(FEATURE_INFO)

# 분봉에서 뽑는 선택 특징 (분봉 데이터가 있는 기간에만 존재)
MINUTE_FEATURE_INFO = {
    "open30_vol_share": ("장초반 30분 거래량 비중", "09:00~09:30 거래량 ÷ 정규장 전체 거래량"),
    "close30_vol_share": ("장마감 30분 거래량 비중", "15:00~15:30 거래량 ÷ 정규장 전체 거래량"),
    "open30_ret": ("장초반 30분 수익률", "09:30 종가 ÷ 09:00 시가 - 1"),
    "intraday_pos": ("일중 고저 내 종가 위치", "(종가-일중저가) ÷ (일중고가-일중저가), 분봉 기준"),
}


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """일봉(date, open, high, low, close, volume; 오름차순) -> 날짜별 특징. 인덱스는 df와 동일.
    t행의 값은 t일 종가까지의 정보로만 계산됩니다 (미래 참조 없음)."""
    o, h, l, c, v = (df[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    prev_close = c.shift(1)
    vol_ma5, vol_ma20 = v.rolling(5).mean(), v.rolling(20).mean()
    ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    low60, high60 = l.rolling(60).min(), h.rolling(60).max()
    vol20_safe = vol_ma20.replace(0, np.nan)

    out = pd.DataFrame(index=df.index)
    out["vol_ratio_5_20"] = vol_ma5 / vol20_safe
    out["vol_ratio_1_20"] = v / vol20_safe
    out["vol_dryup_3_20"] = v.rolling(3).min() / vol20_safe
    out["vol_trend_5"] = vol_ma5 / vol_ma5.shift(5).replace(0, np.nan) - 1
    out["turnover_20"] = np.log((c * v).rolling(20).mean().replace(0, np.nan))
    out["ret_1d"] = c / prev_close - 1
    out["ret_5d"] = c / c.shift(5) - 1
    out["ret_20d"] = c / c.shift(20) - 1
    out["ret_60d"] = c / c.shift(60) - 1
    out["gap_open"] = o / prev_close - 1
    out["body_pct"] = (c - o) / o
    out["upper_wick"] = (h - np.maximum(o, c)) / c
    out["close_ma20_gap"] = c / ma20 - 1
    out["ma5_ma20_gap"] = ma5 / ma20 - 1
    out["ma20_ma60_gap"] = ma20 / ma60 - 1
    out["ma20_slope_5"] = ma20 / ma20.shift(5) - 1
    out["rsi14"] = _rsi(c)
    out["atr14_pct"] = tr.rolling(14).mean() / c
    out["bb_width_20"] = 4 * c.rolling(20).std() / ma20
    out["range_5_pct"] = (h.rolling(5).max() - l.rolling(5).min()) / c
    out["range_pos_60"] = (c - low60) / (high60 - low60).replace(0, np.nan)
    out["dist_high_250"] = c / h.rolling(250, min_periods=120).max() - 1
    return out.replace([np.inf, -np.inf], np.nan)


def compute_outcomes(df: pd.DataFrame, horizons=HORIZONS, stop_pct: float = 2.0,
                     cost_pct: float = 0.41) -> pd.DataFrame:
    """t일 신호 -> t+1 시가 진입 -> 각 보유기간(h)별 결과. 마지막 h일은 결과가 없어 NaN.

    보유기간 h: t+1 시가에 사서 t+h 종가에 팝니다 (h=1이면 진입 당일 종가에 청산 = 당일 시가->종가).
    cost_pct: 왕복 거래비용(%) — 수수료 양쪽 + 매도세 + 슬리피지 양쪽 (config backtest 기본값 합산 ≈ 0.41%).
    stop_pct: 손절선(%) — 진입가 대비 이만큼 아래 저가가 나오면 -stop_pct로 청산했다고 봅니다.
    """
    o, h, l, c = (df[k].astype(float) for k in ("open", "high", "low", "close"))
    entry = o.shift(-1)
    cost, stop = cost_pct / 100, stop_pct / 100
    out = pd.DataFrame(index=df.index)
    for hz in horizons:
        exit_close = c.shift(-hz)
        raw = exit_close / entry - 1
        # 진입일(t+1)부터 청산일(t+h)까지의 최저/최고
        win_low = l.shift(-1).rolling(hz).min().shift(-(hz - 1))
        win_high = h.shift(-1).rolling(hz).max().shift(-(hz - 1))
        mae = win_low / entry - 1
        mfe = win_high / entry - 1
        out[f"ret_{hz}"] = raw - cost
        out[f"mae_{hz}"] = mae
        out[f"mfe_{hz}"] = mfe
        out[f"adj_{hz}"] = np.where(mae <= -stop, -stop - cost, raw - cost)
        out.loc[raw.isna(), [f"adj_{hz}"]] = np.nan
    return out.replace([np.inf, -np.inf], np.nan)


def compute_minute_features(minute: pd.DataFrame) -> pd.DataFrame:
    """1분봉(timestamp, open, high, low, close, volume) -> 날짜별 장중 특징.
    정규장(09:00~15:30) 분봉만 씁니다 (분봉 데이터에는 시간외 봉도 섞여 있음)."""
    if minute.empty:
        return pd.DataFrame(columns=list(MINUTE_FEATURE_INFO))
    m = minute.copy()
    ts = pd.to_datetime(m["timestamp"])
    m["date"] = ts.dt.strftime("%Y-%m-%d")
    hhmm = ts.dt.hour * 100 + ts.dt.minute
    m = m[(hhmm >= 900) & (hhmm <= 1530)].assign(hhmm=hhmm)
    if m.empty:
        return pd.DataFrame(columns=list(MINUTE_FEATURE_INFO))

    g = m.groupby("date")
    total_vol = g["volume"].sum().replace(0, np.nan)
    open30 = m[m["hhmm"] < 930].groupby("date")
    close30 = m[m["hhmm"] >= 1500].groupby("date")
    day_high, day_low = g["high"].max(), g["low"].min()
    day_close = g["close"].last()
    out = pd.DataFrame({
        "open30_vol_share": open30["volume"].sum() / total_vol,
        "close30_vol_share": close30["volume"].sum() / total_vol,
        "open30_ret": open30["close"].last() / open30["open"].first() - 1,
        "intraday_pos": (day_close - day_low) / (day_high - day_low).replace(0, np.nan),
    })
    return out.replace([np.inf, -np.inf], np.nan)
