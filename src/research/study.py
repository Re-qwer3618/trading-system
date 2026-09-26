"""
전략 검증(event study): "이런 조건에서 샀을 때 실제로 나중에 더 올랐나?"를 여러 종목에 걸쳐
통계적으로 확인합니다. 백테스트(한 종목의 한 전략 수익률)와 달리, 어떤 특징이 매수 타이밍의
공통 신호인지를 종목 수백 개 x 수년치에서 찾는 데 목적이 있습니다.

읽는 법 (중요):
- 모든 성과는 "초과수익" 기준입니다: 같은 날 표본 전 종목 평균을 뺀 값. 시장 전체가 오른 날
  산 것이 전략의 공이 되지 않게 합니다.
- t값/양(+)의 달 비율은 "월 단위"로 묶어서 계산합니다. 같은 날 여러 종목의 수익률은 시장 요인으로
  서로 강하게 얽혀 있어서, 종목-일 단위로 t값을 내면 유의성이 크게 부풀려집니다.
- 규칙 탐색은 앞 70% 기간에서 찾고 뒤 30%에서 다시 확인합니다(사이에 보유기간만큼 비움). 뒤 구간에서
  안 통하면 우연히 맞은(과최적화) 규칙일 가능성이 큽니다. 수백 개 조건을 시도하므로 우연히 좋아 보이는
  규칙은 반드시 나옵니다 — "검증 구간에서도 유지" + "종목별로도 일관" 두 가지를 다 만족해야 의미가 있습니다.
- 표본은 지금 상장돼 있는 종목뿐이라 상장폐지 종목이 빠진 생존편향이 있고(수익률이 낙관적),
  수정주가가 완벽하지 않은 구간은 일봉 데이터 오류로 걸러냅니다(하루 ±31% 초과 변동 주변 제외).
"""

import math
import numpy as np
import pandas as pd

from research.features import (FEATURES, FEATURE_INFO, MINUTE_FEATURE_INFO, HORIZONS,
                               compute_features, compute_outcomes, compute_minute_features)

ALL_FEATURE_INFO = {**FEATURE_INFO, **MINUTE_FEATURE_INFO}
_OP_TEXT = {"<=": "≤", ">=": "≥"}


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """순위상관 (scipy 없이: 순위로 바꾼 뒤 피어슨)."""
    return float(a.rank().corr(b.rank()))


def feature_name(f: str) -> str:
    return ALL_FEATURE_INFO.get(f, (f, ""))[0]


# ----------------------------------------------------------------------
# 표본 종목 선정 / 패널 구성
# ----------------------------------------------------------------------

def select_symbols(store, scope: str = "top_cap", n: int = 300, min_days: int = 400, seed: int = 7) -> list[str]:
    """scope: watchlist(관심종목) | top_cap(시가총액 상위 n) | random(무작위 n).
    일봉이 min_days개 이상 있는 종목만 (특징 계산에 250일이 필요)."""
    cat = store.load_catalog("daily")
    ok = set(cat[cat["day_count"].fillna(0) >= min_days]["symbol"])
    universe = set(store.universe_codes()) or ok
    pool = sorted(ok & universe)
    if scope == "watchlist":
        return [s for s in store.get_watchlist() if s in ok]
    if scope == "random":
        return sorted(np.random.default_rng(seed).choice(pool, size=min(n, len(pool)), replace=False).tolist())
    with store._connect() as conn:
        caps = dict(conn.execute("SELECT symbol, market_cap FROM stock_basic_info").fetchall())
    return sorted(pool, key=lambda s: -(caps.get(s) or 0))[:n]


def build_panel(store, symbols: list[str], start_date: str, end_date: str | None = None,
                horizons=HORIZONS, stop_pct: float = 2.0, cost_pct: float = 0.41,
                use_minute: bool = False, progress=None) -> pd.DataFrame:
    """종목 x 날짜 패널: 특징 + 결과(+초과수익 ex_h). 250일 특징 계산용 웜업 구간은 자동으로 더 읽고
    start_date 이전 행은 버립니다."""
    warmup = (pd.Timestamp(start_date) - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
    frames = []
    for i, sym in enumerate(symbols, start=1):
        df = store.load(sym, start_date=warmup)
        if len(df) < 320:
            continue
        df = df[(df["close"] > 0) & (df["open"] > 0)].reset_index(drop=True)
        feats = compute_features(df)
        outs = compute_outcomes(df, horizons, stop_pct, cost_pct)

        # 데이터 오류(미조정 액면분할 등) 방어: 하루 ±31% 초과 변동이 있으면 그 주변(전 60일~후 20일) 제외
        bad = (feats["ret_1d"].abs() > 0.31).astype(int)
        near_bad = bad.rolling(81, min_periods=1).sum().shift(-20).fillna(0) > 0
        no_volume = df["volume"] <= 0

        part = pd.concat([df[["date"]], feats, outs], axis=1)
        part["symbol"] = sym
        part = part[(part["date"] >= start_date) & ~near_bad & ~no_volume]
        if end_date:
            part = part[part["date"] <= end_date]
        if use_minute:
            mf = compute_minute_features(store.load_intraday(sym, "1m", limit=200_000))
            part = part.merge(mf, left_on="date", right_index=True, how="left")
        frames.append(part)
        if progress:
            progress(i, len(symbols))

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    panel["month"] = panel["date"].str.slice(0, 7)
    panel["pos"] = panel.groupby("symbol").cumcount()  # 종목 내 순번 (이벤트 솎아내기용)

    counts = panel.groupby("date")["symbol"].transform("count")
    for hz in horizons:
        col = f"adj_{hz}"
        panel[f"ex_{hz}"] = panel[col] - panel.groupby("date")[col].transform("mean")
        panel.loc[counts < 10, f"ex_{hz}"] = np.nan  # 같은 날 표본이 너무 적으면 초과수익 기준이 불안정
    return panel


# ----------------------------------------------------------------------
# 통계 헬퍼
# ----------------------------------------------------------------------

def _monthly(values: pd.Series, months: pd.Series) -> dict:
    """월별 평균 -> (평균, t값, 양의 달 비율, 개월 수). 월 단위 군집으로 t값 부풀림을 막습니다."""
    m = values.groupby(months).mean().dropna()
    n = len(m)
    if n < 3:
        return {"mean": float(m.mean()) if n else np.nan, "t": np.nan, "pos_share": np.nan, "months": n}
    sd = m.std(ddof=1)
    t = float(m.mean() / (sd / math.sqrt(n))) if sd > 0 else np.nan
    return {"mean": float(m.mean()), "t": t, "pos_share": float((m > 0).mean()), "months": n}


def thin_events(panel: pd.DataFrame, mask: pd.Series, horizon: int) -> pd.Series:
    """같은 종목에서 보유기간(horizon일) 안에 연달아 나온 신호는 하나로 봅니다 (겹치는 구간의 중복 계산 방지)."""
    idx = panel.index[mask]
    sub = panel.loc[idx, ["symbol", "pos"]].sort_values(["symbol", "pos"])
    keep = []
    last_sym, last_pos = None, -10 ** 9
    for i, sym, pos in zip(sub.index, sub["symbol"], sub["pos"]):
        if sym != last_sym or pos - last_pos >= horizon:
            keep.append(i)
            last_sym, last_pos = sym, pos
    out = pd.Series(False, index=panel.index)
    out.loc[keep] = True
    return out


def mask_from_conditions(df: pd.DataFrame, conds: list[dict]) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for c in conds:
        col = df[c["feature"]]
        mask &= (col <= c["value"]) if c["op"] == "<=" else (col >= c["value"])
    return mask


# 값이 비율(0.05 = 5%)이라 %로 보여주는 특징
_PCT_FEATURES = {"vol_trend_5", "ret_1d", "ret_5d", "ret_20d", "ret_60d", "gap_open", "body_pct", "upper_wick",
                 "close_ma20_gap", "ma5_ma20_gap", "ma20_ma60_gap", "ma20_slope_5", "atr14_pct", "bb_width_20",
                 "range_5_pct", "dist_high_250", "open30_ret", "open30_vol_share", "close30_vol_share"}


def format_value(feature: str, value: float) -> str:
    if feature == "turnover_20":  # 로그 거래대금 -> 억원
        return f"{math.exp(value) / 1e8:,.0f}억원"
    if feature in _PCT_FEATURES:
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def describe_conditions(conds: list[dict]) -> str:
    return " 그리고 ".join(
        f"{feature_name(c['feature'])} {_OP_TEXT[c['op']]} {format_value(c['feature'], c['value'])}" for c in conds)


# ----------------------------------------------------------------------
# 1) 특징별 유효성: 이 값이 높을수록/낮을수록 이후 성과가 좋은가
# ----------------------------------------------------------------------

def feature_scan(panel: pd.DataFrame, horizon: int, features: list[str] | None = None) -> pd.DataFrame:
    """특징별로 같은 날 종목들을 5분위(Q1=가장 낮은 값 ... Q5=가장 높은 값)로 나누고 분위별 초과수익을 봅니다.
    '스프레드' = Q5 - Q1 초과수익. 양수면 값이 클수록 좋고, 음수면 값이 작을수록 좋다는 뜻."""
    features = [f for f in (features or FEATURES) if f in panel.columns]
    ex, adj = panel[f"ex_{horizon}"], panel[f"adj_{horizon}"]
    rows = []
    for f in features:
        valid = panel[f].notna() & ex.notna()
        if valid.sum() < 1000:
            continue
        sub = panel.loc[valid]
        pct = sub.groupby("date")[f].rank(pct=True)
        q = pd.cut(pct, [0, .2, .4, .6, .8, 1.0], labels=False, include_lowest=True)
        qmean = ex[valid].groupby(q).mean()
        qwin = (adj[valid] > 0).groupby(q).mean()
        if len(qmean) < 5:
            continue
        monthly = pd.DataFrame({"q": q, "ex": ex[valid], "m": sub["month"]}).groupby(["m", "q"])["ex"].mean().unstack()
        spread_by_month = (monthly[4] - monthly[0]).dropna()
        n_m = len(spread_by_month)
        sd = spread_by_month.std(ddof=1) if n_m >= 3 else np.nan
        row = {
            "feature": f, "name": feature_name(f),
            "ic": float(pct.corr(ex[valid])),
            "spread_pct": float((qmean[4] - qmean[0]) * 100),
            "spread_t": float(spread_by_month.mean() / (sd / math.sqrt(n_m))) if n_m >= 3 and sd > 0 else np.nan,
            "same_sign_months": float((np.sign(spread_by_month) == np.sign(spread_by_month.mean())).mean()) if n_m else np.nan,
            "monotonic": _spearman(pd.Series(range(5)), qmean.reset_index(drop=True)),
            "n": int(valid.sum()),
        }
        for i in range(5):
            row[f"q{i + 1}_ex_pct"] = float(qmean[i] * 100)
            row[f"q{i + 1}_win_pct"] = float(qwin[i] * 100)
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.reindex(out["spread_t"].abs().sort_values(ascending=False).index).reset_index(drop=True)


# ----------------------------------------------------------------------
# 2) 조건(이벤트) 평가
# ----------------------------------------------------------------------

def evaluate_event(panel: pd.DataFrame, mask: pd.Series, horizon: int, train_frac: float = 0.7) -> dict:
    """mask가 True인 날(=매수 신호가 난 날)들의 성과 요약. 겹치는 신호는 솎아내고 계산합니다."""
    adj, ex = f"adj_{horizon}", f"ex_{horizon}"
    valid = panel[adj].notna() & panel[ex].notna()
    base = panel[valid]
    ev_mask = thin_events(panel, mask & valid, horizon)
    ev = panel[ev_mask]
    if len(ev) == 0:
        return {"n": 0}

    mon = _monthly(ev[ex], ev["month"])
    dates = np.sort(panel["date"].unique())
    cut = dates[int(len(dates) * train_frac)]
    tr, te = ev[ev["date"] <= cut], ev[ev["date"] > cut]

    per_symbol = ev.groupby("symbol")[ex].agg(["mean", "count"])
    per_symbol = per_symbol[per_symbol["count"] >= 5]
    by_year = ev.groupby(ev["date"].str.slice(0, 4)).agg(n=(ex, "count"), 초과수익=(ex, lambda s: s.mean() * 100),
                                                          승률=(adj, lambda s: (s > 0).mean() * 100))
    return {
        "n": int(len(ev)), "n_symbols": int(ev["symbol"].nunique()),
        "mean_ret_pct": float(ev[adj].mean() * 100),
        "median_ret_pct": float(ev[adj].median() * 100),
        "win_pct": float((ev[adj] > 0).mean() * 100),
        "excess_pct": float(ev[ex].mean() * 100),
        "base_mean_ret_pct": float(base[adj].mean() * 100),
        "base_win_pct": float((base[adj] > 0).mean() * 100),
        "avg_mae_pct": float(ev[f"mae_{horizon}"].mean() * 100),
        "avg_mfe_pct": float(ev[f"mfe_{horizon}"].mean() * 100),
        "month_t": mon["t"], "pos_months_pct": mon["pos_share"] * 100 if not np.isnan(mon["pos_share"]) else np.nan,
        "months": mon["months"],
        "symbol_pos_pct": float((per_symbol["mean"] > 0).mean() * 100) if len(per_symbol) else np.nan,
        "n_symbols_5plus": int(len(per_symbol)),
        "train_n": int(len(tr)), "train_excess_pct": float(tr[ex].mean() * 100) if len(tr) else np.nan,
        "test_n": int(len(te)), "test_excess_pct": float(te[ex].mean() * 100) if len(te) else np.nan,
        "by_year": by_year.round(2),
    }


# ----------------------------------------------------------------------
# 3) 자주 좋은 매수 시점에 나타나는 특징 (프로필)
# ----------------------------------------------------------------------

def entry_profile(panel: pd.DataFrame, horizon: int, top_frac: float = 0.1,
                  features: list[str] | None = None) -> pd.DataFrame:
    """초과수익 상위 top_frac(좋은 매수 시점)와 하위 top_frac(나쁜 시점)에서 각 특징이 같은 날 종목들 사이에서
    평균적으로 몇 %위치에 있었나(0=가장 낮음, 1=가장 높음, 0.5=차이 없음). 좋은 시점이 0.5에서 멀고
    나쁜 시점과 반대쪽에 있을수록 구분력이 있는 특징입니다. 종목별 일관성은 '좋은 시점이 10번 이상 있던
    종목 중 같은 방향인 종목 비율'."""
    features = [f for f in (features or FEATURES) if f in panel.columns]
    ex = panel[f"ex_{horizon}"]
    ok = ex.notna()
    hi, lo = ex[ok].quantile(1 - top_frac), ex[ok].quantile(top_frac)
    good, bad = ok & (ex >= hi), ok & (ex <= lo)
    rows = []
    for f in features:
        pct = panel.groupby("date")[f].rank(pct=True)
        g, b = pct[good].mean(), pct[bad].mean()
        per_sym = pct[good].groupby(panel.loc[good, "symbol"]).agg(["mean", "count"])
        per_sym = per_sym[per_sym["count"] >= 10]
        direction = np.sign(g - 0.5)
        consist = float((np.sign(per_sym["mean"] - 0.5) == direction).mean() * 100) if len(per_sym) and direction != 0 else np.nan
        rows.append({"feature": f, "name": feature_name(f), "good_pos": float(g), "bad_pos": float(b),
                     "gap": float(g - b), "symbol_consistency_pct": consist, "symbols": int(len(per_sym))})
    out = pd.DataFrame(rows)
    return out.reindex(out["gap"].abs().sort_values(ascending=False).index).reset_index(drop=True)


# ----------------------------------------------------------------------
# 4) 규칙 탐색 (앞 70%에서 찾고 뒤 30%에서 검증)
# ----------------------------------------------------------------------

_LOW_Q, _HIGH_Q = (0.1, 0.2, 0.3), (0.7, 0.8, 0.9)


def discover_rules(panel: pd.DataFrame, horizon: int, features: list[str] | None = None,
                   train_frac: float = 0.7, min_events: int = 300, top_k: int = 15,
                   pairs_from: int = 12) -> tuple[pd.DataFrame, int]:
    """단일 조건(특징 <= 하위분위 / >= 상위분위)과 그 중 상위 조건들의 2개 조합(AND)을 앞 구간(학습)에서 평가해
    초과수익 t값이 높은 순으로 고르고, 그 규칙들을 뒤 구간(검증)에서 다시 평가합니다.
    반환: (규칙 표, 시도한 조건 수). 시도 횟수가 많을수록 우연히 좋은 규칙이 섞일 확률이 커집니다."""
    features = [f for f in (features or FEATURES) if f in panel.columns]
    ex = panel[f"ex_{horizon}"]
    dates = np.sort(panel["date"].unique())
    cut_idx = int(len(dates) * train_frac)
    train_end = dates[cut_idx]
    test_start = dates[min(cut_idx + horizon + 1, len(dates) - 1)]  # 학습/검증 사이를 보유기간만큼 비워 겹침(누수) 방지
    in_train = (panel["date"] <= train_end) & ex.notna()
    in_test = (panel["date"] >= test_start) & ex.notna()
    train = panel[in_train]

    def stats(mask_full: pd.Series, part_mask: pd.Series) -> dict:
        m = mask_full & part_mask
        vals = ex[m]
        if len(vals) == 0:
            return {"n": 0, "excess_pct": np.nan, "t": np.nan, "pos_months": np.nan}
        mon = _monthly(vals, panel.loc[m, "month"])
        return {"n": int(len(vals)), "excess_pct": float(vals.mean() * 100), "t": mon["t"],
                "pos_months": mon["pos_share"] * 100 if not np.isnan(mon["pos_share"]) else np.nan}

    singles = []
    tried = 0
    for f in features:
        col_tr = train[f].dropna()
        if len(col_tr) < 1000:
            continue
        for q in _LOW_Q + _HIGH_Q:
            thr = float(col_tr.quantile(q))
            op = "<=" if q < 0.5 else ">="
            conds = [{"feature": f, "op": op, "value": thr}]
            mask = mask_from_conditions(panel, conds)
            tried += 1
            s = stats(mask, in_train)
            if s["n"] >= min_events and s["t"] == s["t"]:
                singles.append((s["t"], conds, mask, s))

    singles.sort(key=lambda x: -x[0])
    candidates = list(singles)
    top_singles = singles[:pairs_from]
    for i in range(len(top_singles)):
        for j in range(i + 1, len(top_singles)):
            if top_singles[i][1][0]["feature"] == top_singles[j][1][0]["feature"]:
                continue
            mask = top_singles[i][2] & top_singles[j][2]
            tried += 1
            s = stats(mask, in_train)
            if s["n"] >= max(60, min_events // 3) and s["t"] == s["t"]:
                candidates.append((s["t"], top_singles[i][1] + top_singles[j][1], mask, s))

    candidates.sort(key=lambda x: -x[0])
    rows = []
    for t, conds, mask, s_tr in candidates[:top_k]:
        s_te = stats(mask, in_test)
        rows.append({
            "rule": describe_conditions(conds), "conds": conds,
            "train_n": s_tr["n"], "train_excess_pct": s_tr["excess_pct"], "train_t": s_tr["t"],
            "test_n": s_te["n"], "test_excess_pct": s_te["excess_pct"], "test_t": s_te["t"],
            "test_pos_months": s_te["pos_months"],
            "holds": bool(s_te["n"] >= 60 and s_te["excess_pct"] > 0 and (s_te["t"] or 0) >= 1.0),
        })
    return pd.DataFrame(rows), tried


# ----------------------------------------------------------------------
# 5) 펀더멘털(기본정보)과의 연관성 — 종목 단위
# ----------------------------------------------------------------------

def fundamental_link(panel: pd.DataFrame, horizon: int, basic_info: pd.DataFrame,
                     mask: pd.Series | None = None) -> pd.DataFrame:
    """종목별 평균 초과수익(mask가 있으면 그 신호일만)과 PER/PBR/ROE/시가총액의 순위상관.
    주의: 기본정보는 '지금 시점' 스냅샷이라 과거 시점의 값이 아닙니다 — 미래 정보가 섞인 참고용입니다."""
    ex = panel[f"ex_{horizon}"]
    sel = ex.notna() if mask is None else (ex.notna() & mask)
    per_symbol = ex[sel].groupby(panel.loc[sel, "symbol"]).agg(["mean", "count"])
    per_symbol = per_symbol[per_symbol["count"] >= 5]["mean"]
    info = basic_info.set_index("symbol").reindex(per_symbol.index)
    rows = []
    for col, label in (("per", "PER"), ("pbr", "PBR"), ("roe", "ROE"), ("market_cap", "시가총액")):
        x = pd.to_numeric(info[col], errors="coerce")
        ok = x.notna() & (x != 0)
        if ok.sum() >= 30:
            rows.append({"지표": label, "종목수": int(ok.sum()),
                         "순위상관": _spearman(per_symbol[ok], x[ok])})
    return pd.DataFrame(rows)
