"""
이름 붙은 "매수 타이밍" 후보들. 각각 패널(특징 표)에서 "이 날 신호가 났다"를 뜻하는 True/False 열을 만듭니다.
사용자가 직접 떠올린 전략(거래량 축소, 거래량 증가 ...)을 같은 기준으로 나란히 비교하는 용도입니다.
임계값은 출발점일 뿐이고, 검증 화면에서 슬라이더로 바꿔볼 수 있습니다 (study.discover_rules가 데이터에서
임계값을 직접 찾아주기도 합니다).
"""

import pandas as pd


def _ma_cross(panel: pd.DataFrame) -> pd.Series:
    """현재 기본 전략(config strategy.ma_cross)과 같은 신호: 5일선이 20일선을 아래에서 위로 돌파한 날."""
    cur = panel["ma5_ma20_gap"]
    prev = panel.groupby("symbol")["ma5_ma20_gap"].shift(1)
    return (prev <= 0) & (cur > 0)


# 이름 -> (설명, 마스크 함수)
CANDIDATES = {
    "현재 전략: 5/20 골든크로스": (
        "5일선이 20일선을 상향 돌파한 날 (지금 실제로 쓰는 ma_cross 전략의 신호)", _ma_cross),
    "거래량 축소": (
        "최근 5일 평균 거래량이 20일 평균의 60% 이하 (거래가 바짝 마른 상태)",
        lambda p: p["vol_ratio_5_20"] <= 0.6),
    "거래량 축소 + 20일선 위": (
        "거래량 5/20일 ≤ 0.7 이면서 종가가 20일선 위 (마른 채로 추세는 유지)",
        lambda p: (p["vol_ratio_5_20"] <= 0.7) & (p["close_ma20_gap"] >= 0)),
    "눌림목 (상승추세 + 거래량 마른 조정)": (
        "20일선>60일선(상승추세), 종가가 20일선 근처(+2% 이내), 최근 5일 하락, 거래량 5/20일 ≤ 0.8",
        lambda p: (p["ma20_ma60_gap"] >= 0) & (p["close_ma20_gap"] <= 0.02) & (p["close_ma20_gap"] >= -0.03)
                  & (p["ret_5d"] <= 0) & (p["vol_ratio_5_20"] <= 0.8)),
    "거래량 증가": (
        "최근 5일 평균 거래량이 20일 평균의 1.3배 이상",
        lambda p: p["vol_ratio_5_20"] >= 1.3),
    "거래량 증가 + 20일선 위 + 상승기울기": (
        "거래량 5/20일 ≥ 1.3, 종가가 20일선 위, 20일선이 우상향",
        lambda p: (p["vol_ratio_5_20"] >= 1.3) & (p["close_ma20_gap"] >= 0) & (p["ma20_slope_5"] >= 0)),
    "거래량 급증 양봉": (
        "당일 거래량이 20일 평균의 2배 이상 + 몸통 2% 이상 양봉",
        lambda p: (p["vol_ratio_1_20"] >= 2) & (p["body_pct"] >= 0.02)),
    "신고가 부근 + 거래량": (
        "250일 고점의 2% 이내 + 당일 거래량 1.5배 이상",
        lambda p: (p["dist_high_250"] >= -0.02) & (p["vol_ratio_1_20"] >= 1.5)),
    "RSI 과매도 반등": (
        "RSI(14) 30 이하 (낙폭과대)",
        lambda p: p["rsi14"] <= 30),
    "볼린저 수렴 (횡보 압축)": (
        "볼린저 폭이 5% 이하 + 거래량 5/20일 ≤ 0.8 (변동성/거래량 동반 수축)",
        lambda p: (p["bb_width_20"] <= 0.05) & (p["vol_ratio_5_20"] <= 0.8)),
}


def compare_candidates(panel: pd.DataFrame, horizon: int, evaluate) -> pd.DataFrame:
    """모든 후보를 같은 표본/같은 보유기간으로 평가해 한 표로 만듭니다. evaluate = study.evaluate_event"""
    rows = []
    for name, (desc, fn) in CANDIDATES.items():
        r = evaluate(panel, fn(panel).fillna(False), horizon)
        if r.get("n", 0) == 0:
            continue
        rows.append({"전략": name, "설명": desc, **{k: v for k, v in r.items() if k != "by_year"}})
    return pd.DataFrame(rows)
