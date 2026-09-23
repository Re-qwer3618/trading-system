"""
키움증권 HTS(영웅문) 스타일 캔들차트.

일봉 차트와 실시간 분봉 차트가 같은 모양을 쓰도록 여기 한 곳에 모았습니다.
HTS 화면을 흉내 낸 포인트:
- 흰 배경 + 옅은 점선 격자, 가격축은 오른쪽
- 양봉(종가>=시가) 빨강 / 음봉 파랑, 몸통 채움
- 거래량 막대도 그날 캔들 색을 그대로 따라감
- 이동평균선 5/10/20/60/120 (색은 영웅문 기본값과 비슷하게)
- 휴장일/장외시간 빈칸 없이 봉이 붙어서 이어짐 (x축을 카테고리로)
- 오른쪽 축에 현재가 라벨, 화면 구간의 최고/최저가 화살표 표시
- 마우스를 올리면 십자선 + 시/고/저/종/거래량/전일대비
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

UP_COLOR = "#E0161C"    # 양봉 (키움 빨강)
DOWN_COLOR = "#0B45D8"  # 음봉 (키움 파랑)

# 영웅문 기본 이동평균선 색상과 비슷하게 맞춘 값
MA_COLORS = {
    5: "#E62EE6",    # 분홍
    10: "#1E5BFF",   # 파랑
    20: "#F29B00",   # 주황
    60: "#16A34A",   # 초록
    120: "#8B8B8B",  # 회색
}

_THEMES = {
    "light": dict(bg="#FFFFFF", grid="#E5E7EB", axis="#9CA3AF", text="#111827", hover_bg="#FFFFFF"),
    "dark": dict(bg="#0F1117", grid="#2A2E3A", axis="#4B5563", text="#E5E7EB", hover_bg="#1A1D27"),
}


def _fmt_price(v: float) -> str:
    return f"{v:,.0f}"


def kiwoom_candle_chart(
    df: pd.DataFrame,
    x_col: str,
    ma_periods: tuple[int, ...] = (5, 10, 20, 60, 120),
    visible_bars: int | None = None,
    x_label_fmt: str = "%Y/%m/%d",
    tick_label_fmt: str = "%m/%d",
    height: int = 560,
    dark: bool = False,
) -> go.Figure:
    """OHLCV DataFrame을 키움 HTS 스타일 캔들+거래량 차트로 그립니다.

    df         : x_col, open, high, low, close, volume 컬럼 (시간 오름차순)
    tick_label_fmt: x축 눈금에 쓰는 짧은 날짜 형식 (툴팁은 x_label_fmt)
    visible_bars: 마지막 N개 봉만 보여줌. 이동평균은 잘라내기 전 전체 데이터로 계산하므로
                  120일선처럼 긴 선도 화면 첫 봉부터 제대로 그려집니다.
    """
    t = _THEMES["dark" if dark else "light"]
    df = df.copy().reset_index(drop=True)
    df["_x"] = pd.to_datetime(df[x_col])
    df["_prev_close"] = df["close"].shift(1)
    for p in ma_periods:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    if visible_bars:
        df = df.tail(visible_bars).reset_index(drop=True)

    # 카테고리 x축: 휴장일/장외시간이 빈칸으로 벌어지지 않고 봉이 연속으로 붙음
    x = df["_x"].dt.strftime(x_label_fmt)
    is_up = df["close"] >= df["open"]
    bar_colors = [UP_COLOR if u else DOWN_COLOR for u in is_up]

    chg = df["close"] - df["_prev_close"]
    chg_pct = chg / df["_prev_close"] * 100
    hover = [
        f"<b>{xi}</b><br>"
        f"시가 {_fmt_price(o)}<br>고가 {_fmt_price(h)}<br>저가 {_fmt_price(l)}<br>종가 {_fmt_price(c)}<br>"
        + (f"대비 {d:+,.0f} ({dp:+.2f}%)<br>" if pd.notna(d) else "")
        + f"거래량 {v:,.0f}"
        for xi, o, h, l, c, v, d, dp in zip(
            x, df["open"], df["high"], df["low"], df["close"], df["volume"], chg, chg_pct
        )
    ]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24], vertical_spacing=0.02)

    fig.add_trace(go.Candlestick(
        x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing=dict(line=dict(color=UP_COLOR, width=1), fillcolor=UP_COLOR),
        decreasing=dict(line=dict(color=DOWN_COLOR, width=1), fillcolor=DOWN_COLOR),
        text=hover, hoverinfo="text", name="가격",
    ), row=1, col=1)

    for p in ma_periods:
        if df[f"ma{p}"].notna().any():
            fig.add_trace(go.Scatter(
                x=x, y=df[f"ma{p}"], mode="lines", name=f"{p}",
                line=dict(color=MA_COLORS.get(p, "#6B7280"), width=1.2), hoverinfo="skip",
            ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=x, y=df["volume"], marker=dict(color=bar_colors, line=dict(width=0)),
        hoverinfo="skip", name="거래량",
    ), row=2, col=1)

    # 화면 구간 최고/최저가 표시 (HTS의 "최고 xxx / 최저 xxx" 화살표)
    hi_i, lo_i = df["high"].idxmax(), df["low"].idxmin()
    fig.add_annotation(
        x=x[hi_i], y=df["high"][hi_i], text=f"최고 {_fmt_price(df['high'][hi_i])}",
        showarrow=True, arrowhead=2, arrowsize=0.8, ax=0, ay=-22,
        font=dict(size=11, color=UP_COLOR), arrowcolor=UP_COLOR, row=1, col=1,
    )
    fig.add_annotation(
        x=x[lo_i], y=df["low"][lo_i], text=f"최저 {_fmt_price(df['low'][lo_i])}",
        showarrow=True, arrowhead=2, arrowsize=0.8, ax=0, ay=22,
        font=dict(size=11, color=DOWN_COLOR), arrowcolor=DOWN_COLOR, row=1, col=1,
    )

    # 현재가 라인 + 오른쪽 축 라벨 박스
    last = df.iloc[-1]
    last_color = UP_COLOR if last["close"] >= last["open"] else DOWN_COLOR
    fig.add_hline(y=last["close"], line=dict(color=last_color, width=1, dash="dot"), row=1, col=1)
    fig.add_annotation(
        xref="paper", x=1.0, xanchor="left", y=last["close"], yref="y",
        text=f" {_fmt_price(last['close'])} ", showarrow=False,
        font=dict(size=11, color="#FFFFFF"), bgcolor=last_color,
    )

    # 좌상단 이동평균 범례 (HTS처럼 "이동평균 5 10 20 60 120"을 각 색으로)
    ma_legend = " ".join(
        f"<span style='color:{MA_COLORS.get(p, '#6B7280')}'>{p}</span>"
        for p in ma_periods if df[f"ma{p}"].notna().any()
    )
    if ma_legend:
        fig.add_annotation(
            xref="paper", yref="paper", x=0.0, y=1.0, xanchor="left", yanchor="bottom",
            text=f"이동평균 {ma_legend}", showarrow=False, font=dict(size=11, color=t["text"]),
        )

    axis_common = dict(
        showgrid=True, gridcolor=t["grid"], griddash="dot", linecolor=t["axis"], showline=True,
        mirror=True, tickfont=dict(size=10, color=t["text"]), zeroline=False,
        showspikes=True, spikemode="across", spikesnap="cursor", spikethickness=1,
        spikecolor=t["axis"], spikedash="solid",
    )
    # 카테고리 축은 라벨이 빽빽해지므로 8개 정도만 골라서 표시
    # (x 값은 툴팁용 긴 형식, 축 눈금에는 tick_label_fmt의 짧은 형식을 씀)
    step = max(1, len(x) // 7)
    fig.update_xaxes(
        type="category", rangeslider_visible=False, tickmode="array",
        tickvals=list(x[::step]), ticktext=list(df["_x"].dt.strftime(tick_label_fmt)[::step]),
        tickangle=0, **axis_common,
    )
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig.update_yaxes(side="right", tickformat=",", **axis_common)
    fig.update_yaxes(tickformat="~s", row=2, col=1)

    fig.update_layout(
        height=height, paper_bgcolor=t["bg"], plot_bgcolor=t["bg"],
        font=dict(color=t["text"], family="Malgun Gothic, 맑은 고딕, sans-serif"),
        margin=dict(l=10, r=70, t=24, b=10), showlegend=False,
        hovermode="x", hoverlabel=dict(bgcolor=t["hover_bg"], font_size=12, font_color=t["text"]),
        bargap=0.2, dragmode="pan",
    )
    return fig


def _price_color(price: float, ref: float | None, t: dict) -> str:
    """기준가(전일종가) 대비 상승 빨강 / 하락 파랑 / 보합 기본색 — HTS 가격 색 규칙."""
    if ref is None or price == ref:
        return t["text"]
    return UP_COLOR if price > ref else DOWN_COLOR


def _to_qty(v) -> int | None:
    try:
        return None if v is None or pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


def kiwoom_orderbook_html(
    book: dict,
    ref_price: float | None = None,
    last_price: float | None = None,
    levels: int = 10,
    dark: bool = False,
) -> str:
    """키움 HTS 10단 호가창 모양의 HTML을 만듭니다 (st.markdown(unsafe_allow_html=True)로 표시).

    - 가운데 호가, 위쪽 절반은 매도(왼쪽에 잔량), 아래쪽 절반은 매수(오른쪽에 잔량)
    - 잔량은 가장 큰 잔량 대비 비율 막대로 표시 (매도 파랑 / 매수 빨강)
    - 호가 숫자 색과 등락률은 기준가(ref_price, 보통 전일종가) 대비
    - 현재가(last_price)와 같은 호가 칸은 테두리로 강조
    - 맨 아래 총잔량 / 잔량 차이
    """
    t = _THEMES["dark" if dark else "light"]
    ask_bg = "#1B2540" if dark else "#EEF4FF"
    bid_bg = "#3A1D22" if dark else "#FFF0F0"
    ask_bar = "rgba(11,69,216,0.22)" if not dark else "rgba(59,130,246,0.35)"
    bid_bar = "rgba(224,22,28,0.18)" if not dark else "rgba(239,68,68,0.35)"

    asks = list(book.get("asks") or [])[:levels]   # 1호가(최저가)부터
    bids = list(book.get("bids") or [])[:levels]   # 1호가(최고가)부터
    max_qty = max([q for _, q in asks + bids] + [1])

    def price_cell(price: float, bg: str) -> str:
        color = _price_color(price, ref_price, t)
        rate = f"{(price - ref_price) / ref_price * 100:+.2f}%" if ref_price else ""
        border = f"outline:2px solid {t['text']};outline-offset:-2px;" if last_price is not None and price == last_price else ""
        return (
            f"<td style='background:{bg};text-align:right;color:{color};font-weight:600;{border}'>"
            f"{price:,.0f}<span style='font-size:10px;font-weight:400;margin-left:6px;opacity:.85'>{rate}</span></td>"
        )

    def qty_cell(qty: int, bar: str, align: str) -> str:
        pct = qty / max_qty * 100
        # 매도 잔량 막대는 오른쪽(호가 쪽)에서 왼쪽으로, 매수는 왼쪽(호가 쪽)에서 오른쪽으로 자람
        direction = "to left" if align == "right" else "to right"
        gradient = f"linear-gradient({direction}, {bar} {pct:.1f}%, transparent {pct:.1f}%)"
        return f"<td style='text-align:{align};background:{gradient};color:{t['text']}'>{qty:,}</td>"

    empty = "<td></td>"
    rows = []
    for price, qty in reversed(asks):  # 화면 위쪽이 높은 가격(10호가)
        rows.append(f"<tr>{qty_cell(qty, ask_bar, 'right')}{price_cell(price, ask_bg)}{empty}</tr>")
    for price, qty in bids:
        rows.append(f"<tr>{empty}{price_cell(price, bid_bg)}{qty_cell(qty, bid_bar, 'left')}</tr>")

    total_ask = _to_qty(book.get("total_ask_qty"))
    total_bid = _to_qty(book.get("total_bid_qty"))
    diff = (total_bid - total_ask) if total_ask is not None and total_bid is not None else None
    diff_color = UP_COLOR if diff and diff > 0 else DOWN_COLOR if diff and diff < 0 else t["text"]
    fmt = lambda v: f"{v:,}" if v is not None else "-"

    return f"""
<table class='kw-book' style='width:100%;border-collapse:collapse;font-size:12.5px;font-family:"Malgun Gothic",sans-serif;
              font-variant-numeric:tabular-nums;background:{t["bg"]};border:1px solid {t["axis"]}'>
  <colgroup><col style='width:32%'><col style='width:36%'><col style='width:32%'></colgroup>
  <thead><tr style='color:{t["text"]};border-bottom:1px solid {t["axis"]}'>
    <th style='text-align:center;padding:4px;color:{DOWN_COLOR}'>매도잔량</th>
    <th style='text-align:center;padding:4px'>호가</th>
    <th style='text-align:center;padding:4px;color:{UP_COLOR}'>매수잔량</th>
  </tr></thead>
  <tbody style='line-height:1.55'>{''.join(rows)}</tbody>
  <tfoot><tr style='border-top:1px solid {t["axis"]};font-weight:600'>
    <td style='text-align:right;color:{DOWN_COLOR};padding:4px 6px'>{fmt(total_ask)}</td>
    <td style='text-align:center;color:{diff_color};font-size:11px'>잔량차 {(f"{diff:+,}" if diff is not None else "-")}</td>
    <td style='text-align:left;color:{UP_COLOR};padding:4px 6px'>{fmt(total_bid)}</td>
  </tr></tfoot>
</table>
<style>
  .kw-book {{ margin:0 !important; }}
  .kw-book th, .kw-book td {{ padding:1px 6px !important; border:none !important;
                              border-bottom:1px solid {t["grid"]} !important; }}
  .kw-book thead th {{ border-bottom:1px solid {t["axis"]} !important; }}
  .kw-book tfoot td {{ border-top:1px solid {t["axis"]} !important; border-bottom:none !important; padding:4px 6px !important; }}
</style>
"""
