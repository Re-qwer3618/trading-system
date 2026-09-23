"""
백테스트 기반 위원(분석가) 파라미터 튜닝.

지금은 "차트 분석가-1 (과거, history_analyst)"의 추세 판정 파라미터
(sma_short/sma_mid/sma_long)만 튜닝합니다. 이유: 이 세 값만 실제로
매수/매도 시점(=백테스트로 검증 가능한 것)을 바꿉니다. history_analyst의
나머지 파라미터(변동성/박스권/거래량 관련)와 realtime/comparison 위원의
파라미터는 신호 자체를 바꾸지 않거나(변동성 등은 참고용 지표일 뿐) 과거
틱 데이터가 없어서(realtime, comparison) 백테스트로 검증할 방법이 아직
없습니다. 실시간 틱이 realtime_ticks 테이블에 충분히 쌓이면 그 데이터로
realtime/comparison 위원도 같은 방식으로 확장할 수 있습니다.

핵심 아이디어:
1. 여러 (sma_short, sma_mid, sma_long) 조합을 그리드서치.
2. 각 조합으로 "과거 추세를 따라가는 단순 매매"를 백테스트해서
   수익률/승률/최대낙폭을 계산.
3. 벤치마크(매수 후 보유)보다 잘했으면 SUCCESS, 못했으면 FAILURE로 기록.
4. 같은 조합을 나중에 다른 기간으로 다시 튜닝했을 때 SUCCESS<->FAILURE가
   뒤집히면 "전환(transition)"으로 별도 기록 — 시장 국면이 바뀌어서
   예전에 통하던 설정이 더는 안 통한다는 신호입니다.
5. 모든 기록은 사람이 읽는 summary 문장과 함께 저장되어, 나중에
   llm/advisor.py가 실제 LLM을 붙일 때 그대로 컨텍스트로 재사용할 수
   있습니다 (MarketDataStore.tuning_llm_context 참고).
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pandas as pd

ANALYST_NAME = "history"

# 명시적으로 그리드를 안 주면 이 범위로 탐색합니다.
_DEFAULT_GRID = {
    "sma_short": [3, 5, 10],
    "sma_mid": [15, 20, 30],
    "sma_long": [40, 60, 90],
}


def _trend_series(close: pd.Series, sma_short: int, sma_mid: int, sma_long: int) -> pd.Series:
    """일자별 추세 상태: 1=상승 정배열, -1=하락 역배열, 0=혼조."""
    short_ma = close.rolling(sma_short).mean()
    mid_ma = close.rolling(sma_mid).mean()
    long_ma = close.rolling(sma_long).mean()
    trend = pd.Series(0, index=close.index)
    trend[(short_ma > mid_ma) & (mid_ma > long_ma)] = 1
    trend[(short_ma < mid_ma) & (mid_ma < long_ma)] = -1
    trend[long_ma.isna()] = pd.NA
    return trend


def backtest_history_params(df: pd.DataFrame, params: dict, starting_cash: float = 10_000_000.0) -> dict | None:
    """
    "상승 정배열일 때만 보유"하는 단순 롱온리 규칙으로 백테스트합니다.
    (실전 main.py/DecisionMaker의 규칙과 완전히 같은 코드는 아니지만,
    history_analyst가 판단하는 바로 그 추세 신호를 그대로 씁니다.)

    데이터가 부족하면 None을 반환합니다 (그리드서치에서 건너뜀).
    """
    sma_short, sma_mid, sma_long = params["sma_short"], params["sma_mid"], params["sma_long"]
    if not (sma_short < sma_mid < sma_long):
        return None
    if len(df) < sma_long + 5:
        return None

    close = df["close"].reset_index(drop=True)
    dates = df["date"].reset_index(drop=True)
    trend = _trend_series(close, sma_short, sma_mid, sma_long)

    first_valid = trend.first_valid_index()
    if first_valid is None:
        return None

    cash = starting_cash
    position = 0
    entry_price = None
    trade_pnls = []
    equity_curve = []

    for i in range(first_valid, len(close)):
        price = float(close.iloc[i])
        state = trend.iloc[i]

        if state == 1 and position == 0:
            qty = int(cash // price)
            if qty > 0:
                position = qty
                cash -= qty * price
                entry_price = price
        elif state != 1 and position > 0:
            cash += position * price
            trade_pnls.append((price - entry_price) * position)
            position = 0
            entry_price = None

        equity_curve.append(cash + position * price)

    # 마지막까지 보유 중이면 마감 시점 가격으로 청산한 것으로 간주(평가손익 반영, 거래횟수엔 미포함)
    final_close = float(close.iloc[-1])
    final_value = cash + position * final_close

    total_return_pct = round((final_value / starting_cash - 1) * 100, 2)

    bench_start = float(close.iloc[first_valid])
    benchmark_return_pct = round((final_close / bench_start - 1) * 100, 2)

    num_trades = len(trade_pnls)
    win_rate_pct = round(sum(1 for p in trade_pnls if p > 0) / num_trades * 100, 1) if num_trades else None

    if equity_curve:
        eq = pd.Series(equity_curve)
        running_max = eq.cummax()
        drawdown = (eq - running_max) / running_max
        max_drawdown_pct = round(float(drawdown.min()) * 100, 2)
    else:
        max_drawdown_pct = 0.0

    return {
        "total_return_pct": total_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
        "num_trades": num_trades,
        "win_rate_pct": win_rate_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "start_date": str(dates.iloc[first_valid]),
        "end_date": str(dates.iloc[-1]),
    }


def _decide_outcome(metrics: dict) -> str:
    if metrics["num_trades"] == 0:
        return "FAILURE"
    return "SUCCESS" if metrics["total_return_pct"] > metrics["benchmark_return_pct"] else "FAILURE"


def _summarize(symbol: str, params: dict, metrics: dict, outcome: str) -> str:
    win_txt = f", 승률 {metrics['win_rate_pct']}%" if metrics["win_rate_pct"] is not None else ""
    return (
        f"{symbol} sma({params['sma_short']}/{params['sma_mid']}/{params['sma_long']}) "
        f"{metrics['start_date']}~{metrics['end_date']}: 수익률 {metrics['total_return_pct']}% "
        f"(매수&보유 {metrics['benchmark_return_pct']}%), 거래 {metrics['num_trades']}회{win_txt} "
        f"→ {outcome}"
    )


class HistoryAnalystTuner:
    """history_analyst의 sma_short/sma_mid/sma_long을 백테스트로 그리드서치합니다."""

    def __init__(self, store):
        self.store = store

    def grid_search(
        self,
        symbol: str,
        param_grid: dict | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        starting_cash: float = 10_000_000.0,
        stability_penalty: float = 2.0,
    ) -> list[dict]:
        """
        모든 조합을 백테스트하고, 성공/실패를 DB에 기록합니다.
        같은 조합이 예전에도 튜닝된 적 있고 이번에 결과(성공/실패)가 달라졌다면
        "전환"으로도 별도 기록합니다.

        반환값: 각 조합의 결과 리스트. score(=수익률 - 불안정 페널티)가 높은 순으로 정렬.
        """
        grid = param_grid or _DEFAULT_GRID
        df = self.store.load(symbol, start_date)
        if end_date:
            df = df[df["date"] <= end_date].reset_index(drop=True)

        if df.empty:
            raise ValueError(f"{symbol}의 저장된 일봉 데이터가 없습니다. 먼저 run.bat collect {symbol} 을 실행하세요.")

        combos = [
            dict(zip(grid.keys(), values))
            for values in itertools.product(*grid.values())
        ]

        results = []
        for params in combos:
            metrics = backtest_history_params(df, params, starting_cash)
            if metrics is None:
                continue  # 잘못된 조합(예: sma_short >= sma_mid) 또는 데이터 부족 — 건너뜀

            outcome = _decide_outcome(metrics)
            summary = _summarize(symbol, params, metrics, outcome)

            previous = self.store.find_previous_tuning_run(symbol, ANALYST_NAME, params)
            run_id = self.store.log_tuning_run(
                symbol, ANALYST_NAME, params, metrics["start_date"], metrics["end_date"], metrics, outcome, summary,
            )

            if previous is not None and previous["outcome"] != outcome:
                new_period = f"{metrics['start_date']}~{metrics['end_date']}"
                transition_summary = (
                    f"{symbol} sma({params['sma_short']}/{params['sma_mid']}/{params['sma_long']}): "
                    f"{previous['start_date']}~{previous['end_date']} 기간엔 {previous['outcome']}였는데, "
                    f"{new_period} 기간엔 {outcome}로 바뀜 (시장 국면 변화 가능성 — 이 설정을 계속 믿지 말 것)."
                )
                self.store.log_tuning_transition(
                    symbol, ANALYST_NAME, params, previous, run_id, outcome, new_period, transition_summary,
                )

            flip_count = self.store.param_set_flip_count(symbol, ANALYST_NAME, params)
            score = metrics["total_return_pct"] - flip_count * stability_penalty

            results.append({
                "params": params,
                "metrics": metrics,
                "outcome": outcome,
                "flip_count": flip_count,
                "score": round(score, 2),
                "summary": summary,
                "run_id": run_id,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def apply_best(self, results: list[dict], config_path: str | Path) -> dict | None:
        """
        grid_search() 결과 중 1위(성공했고 가장 안정적/수익률 높은 조합)를
        config/base.yaml의 analysts.history.sma_short/sma_mid/sma_long에 반영합니다.
        YAML 전체를 다시 쓰지 않고 해당 세 줄만 정규식으로 치환해서 주석/서식을 보존합니다.
        """
        candidates = [r for r in results if r["outcome"] == "SUCCESS"]
        if not candidates:
            return None
        best = candidates[0]

        path = Path(config_path)
        text = path.read_text(encoding="utf-8")
        for key in ("sma_short", "sma_mid", "sma_long"):
            value = best["params"][key]
            text, n = re.subn(rf"(?m)^(\s*{key}:\s*)\S+", rf"\g<1>{value}", text, count=1)
            if n == 0:
                raise RuntimeError(f"config에서 '{key}:' 줄을 찾지 못했습니다. base.yaml 형식이 바뀌었을 수 있습니다.")
        path.write_text(text, encoding="utf-8")
        return best
