"""
데이터 카탈로그 + 종목 데이터 수집 관리 페이지.

- 현황: 어떤 종목에 어떤 데이터(일봉/분봉/틱/호가/기본정보)가 어디부터 어디까지 있는지 (data_catalog)
- 수집 실행: 누락/부족 종목을 골라 백그라운드에서 수집 (브라우저를 닫아도 계속 진행, collect_worker.py)
           + 실시간 틱을 종목별 분봉에 이어붙이기 (close_day.py)
- 관심종목·전략: 실시간 매매/수집 대상(watchlist)을 전략 그룹으로 나눠 관리, 전략 신호로 후보 찾기
- 작업 이력: 지난 수집 작업, 실패 종목 재시도
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import math
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from ui_common import require_login, render_header
from config_loader import load_config
from data_layer.storage import MarketDataStore
from data_layer.catalog import catalog_matrix, find_gaps, refresh_catalog, REASON_LABELS
from data_layer.collector import CollectOptions, DATASET_LABELS, MAX_TARGET_DAYS, prioritize
from data_layer.jobs import create_and_launch, request_cancel, kill_worker, JobAlreadyRunning
from close_day import merge_realtime
from strategy_scan import scan_signals

st.set_page_config(page_title="데이터 수집", page_icon="🗄️", layout="wide")
require_login()
render_header("🗄️ 데이터 수집 · 카탈로그")

config = load_config()
store = MarketDataStore(config["data"]["db_path"])
intraday_cfg = config.get("data", {}).get("intraday", {})

_STATUS_LABEL = {"pending": "대기", "running": "실행 중", "cancelling": "중지 중", "done": "완료",
                 "cancelled": "중지됨", "failed": "실패", "aborted": "비정상 종료"}
_MINUTE_DEPTH_CHOICES = {"증분만 (최근 것만 이어받기)": 0, "30일": 30, "60일": 60, "180일": 180,
                         "1년 (서버가 가진 가장 오래된 분봉까지)": MAX_TARGET_DAYS}

matrix = catalog_matrix(store)
names = dict(zip(matrix["symbol"], matrix["name"].fillna("")))


def _label(symbol: str) -> str:
    return f"{symbol} {names.get(symbol, '')}".strip()


def _fmt_seconds(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return f"{sec}초"
    if sec < 5400:
        return f"{sec // 60}분"
    return f"{sec / 3600:.1f}시간"


def _estimate_seconds(symbols: list[str], opts: CollectOptions) -> float:
    """대략의 소요시간. 키움 호출 제한이 페이지당 약 1초라서 (페이지 수 x 1초)로 어림합니다.
    분봉 과거 확장은 실측상 하루당 약 0.3페이지 (005930: 113페이지 ≈ 1년)로 봅니다."""
    sub = matrix[matrix["symbol"].isin(symbols)]
    n = len(sub)
    sec = 0.0
    if "daily" in opts.datasets:
        sec += n * (20 if opts.full else 1.3)
    if "minute" in opts.datasets:
        if opts.minute_target_days > 0:
            start = (datetime.now() - timedelta(days=opts.minute_target_days)).strftime("%Y-%m-%d")
            need = sub[(~sub["m1_exhausted"]) & (sub["m1_first"].isna() | (sub["m1_first"] > start))]
            pages = min(113, math.ceil(0.3 * min(opts.minute_target_days, 365)))
            sec += len(need) * pages * 1.05 + (n - len(need)) * 1.3
        else:
            sec += n * 1.3
    if "tick" in opts.datasets:
        sec += n * 3.3
    if "info" in opts.datasets:
        sec += n * 1.1
    return sec + n * opts.sleep_sec


# ---------------------------------------------------------------------------
# 진행 중인 수집 작업 (3초마다 자동 갱신)
# ---------------------------------------------------------------------------
@st.fragment(run_every="3s")
def render_active_job():
    job = store.active_job()
    if job is None:
        if st.session_state.pop("job_was_active", False):
            st.rerun()  # 작업이 방금 끝남 -> 시작 버튼/카탈로그를 새로 그리기 위해 전체 갱신
        return
    st.session_state["job_was_active"] = True

    total, done = job["total"] or 0, job["done"] or 0
    st.subheader(f"🚚 수집 진행 중 — 작업 #{job['id']} · {_STATUS_LABEL.get(job['status'], job['status'])}")
    st.progress(done / total if total else 0.0, text=f"{done:,} / {total:,} 종목 (성공 {job['ok']:,} · 실패 {job['failed']:,})")

    eta_text = ""
    if job.get("started_at") and done > 0:
        elapsed = (datetime.now() - datetime.fromisoformat(job["started_at"])).total_seconds()
        eta_text = f" · 경과 {_fmt_seconds(elapsed)} · 남은 시간 약 {_fmt_seconds(elapsed / done * (total - done))}"
    st.caption(f"{job['title']}{eta_text}")
    if job.get("current_symbol"):
        st.caption(f"다음 종목: {_label(job['current_symbol'])}")
    if job.get("message"):
        st.caption(f"최근: {job['message']}")

    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("⏹ 중지", key="cancel_job", disabled=job["status"] == "cancelling",
                 help="현재 페이지까지 마치고 멈춥니다. 지금까지 받은 데이터는 저장됩니다."):
        request_cancel(store, job["id"])
    with c2.popover("강제 종료"):
        st.caption("워커가 응답하지 않을 때만 쓰세요. 이미 저장된 데이터는 그대로 남습니다.")
        if st.button("지금 강제 종료", key="kill_job", type="primary"):
            kill_worker(store, job["id"])
            st.rerun()
    st.markdown("---")


render_active_job()
active_job = store.active_job()

tab_status, tab_run, tab_watch, tab_history = st.tabs(
    ["📊 데이터 현황", "🚚 수집 실행", "⭐ 관심종목 · 전략", "📜 작업 이력"])

# ===========================================================================
# 탭 1: 데이터 현황 (카탈로그)
# ===========================================================================
with tab_status:
    summary_cols = st.columns(7)
    counts = [
        ("수집 종목", len(matrix)),
        ("일봉", int(matrix["daily_days"].notna().sum())),
        ("1분봉", int(matrix["m1_days"].notna().sum())),
        ("틱봉", int(matrix["t1_days"].notna().sum())),
        ("실시간 체결", int(matrix["rt_tick_rows"].notna().sum())),
        ("실시간 호가", int(matrix["rt_ob_rows"].notna().sum())),
        ("기본정보", int(matrix["info_at"].notna().sum())),
    ]
    for col, (label, value) in zip(summary_cols, counts):
        col.metric(label, f"{value:,}")

    cat_all = store.load_catalog()
    refreshed = cat_all["refreshed_at"].max() if not cat_all.empty else None
    sizes = {Path(f).name: Path(f).stat().st_size / 1024 ** 3 for f in dict.fromkeys(store.files.values())
             if Path(f).exists()}
    size_text = " · ".join(f"{name} {gb:,.2f}GB" for name, gb in sizes.items())
    info_col, btn_col = st.columns([4, 1])
    info_col.caption(f"카탈로그 집계 시각: {refreshed or '아직 없음'} · DB: {size_text} "
                     f"({'분리 모드' if store.split else '단일 파일 모드'}, 총 {sum(sizes.values()):,.2f}GB)")
    if btn_col.button("🔄 전체 재집계", help="원본 테이블을 다시 훑어 카탈로그를 새로 만듭니다 (약 15~30초)."):
        with st.spinner("전체 재집계 중..."):
            result = refresh_catalog(store)
        st.success(f"{result['rows']:,}행 집계 완료 ({result['seconds']}초)"
                   + (f" · 잘못된 행 정리: {result['purged']}" if any(result["purged"].values()) else ""))
        st.rerun()

    st.markdown("##### 수집이 필요한 종목")
    min_days = st.slider("분봉이 이 거래일 수보다 짧으면 '얕음'으로 봅니다", 5, 250, 60, step=5,
                         help="서버가 더 오래된 분봉을 안 주는 종목(끝까지 받은 종목)은 얕아도 제외됩니다.")
    gaps = find_gaps(store, min_minute_days=min_days, matrix=matrix)
    reason_counts = {label: int(gaps["reasons"].map(lambda r, k=key: k in r).sum())
                     for key, label in REASON_LABELS.items()}
    gap_cols = st.columns(len(reason_counts) + 1)
    gap_cols[0].metric("수집 필요 종목", f"{len(gaps):,}")
    for col, (label, cnt) in zip(gap_cols[1:], reason_counts.items()):
        col.metric(label, f"{cnt:,}")

    st.markdown("##### 1분봉 커버 기간 분포 (거래일 수)")
    depth = matrix["m1_days"].dropna()
    if depth.empty:
        st.caption("1분봉 데이터가 없습니다.")
    else:
        buckets = pd.cut(depth, bins=[0, 5, 20, 60, 120, 250, 10000],
                         labels=["5일 이하", "6~20일", "21~60일", "61~120일", "121~250일", "250일 초과"])
        st.bar_chart(buckets.value_counts().sort_index(), height=200)

    st.markdown("##### 종목별 현황")
    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
    query = f1.text_input("검색 (코드 또는 이름)", key="catalog_query")
    only_watch = f2.toggle("관심종목만", key="catalog_only_watch")
    only_gap = f3.toggle("수집 필요만", key="catalog_only_gap")
    market_pick = f4.selectbox("시장", ["전체", "코스피", "코스닥"], key="catalog_market")

    view = matrix.copy()
    gap_reason_map = dict(zip(gaps["symbol"], gaps["reasons"]))
    view["필요"] = view["symbol"].map(
        lambda s: ", ".join(REASON_LABELS[r] for r in gap_reason_map.get(s, [])))
    if query:
        q = query.strip()
        view = view[view["symbol"].str.contains(q, case=False) | view["name"].fillna("").str.contains(q, case=False)]
    if only_watch:
        view = view[view["watch"]]
    if only_gap:
        view = view[view["필요"] != ""]
    if market_pick != "전체":
        view = view[view["market"] == {"코스피": "0", "코스닥": "10"}[market_pick]]

    show = view[["symbol", "name", "market", "watch", "daily_first", "daily_last", "daily_days",
                 "m1_first", "m1_last", "m1_days", "m1_exhausted", "t1_days", "rt_tick_rows", "rt_ob_rows",
                 "info_at", "필요"]].copy()
    show["market"] = show["market"].map({"0": "코스피", "10": "코스닥"}).fillna("-")
    show = show.rename(columns={
        "symbol": "코드", "name": "종목명", "market": "시장", "watch": "관심",
        "daily_first": "일봉 시작", "daily_last": "일봉 끝", "daily_days": "일봉 수",
        "m1_first": "분봉 시작", "m1_last": "분봉 끝", "m1_days": "분봉 거래일",
        "m1_exhausted": "분봉 서버끝", "t1_days": "틱 거래일", "rt_tick_rows": "실시간 체결",
        "rt_ob_rows": "실시간 호가", "info_at": "기본정보 시각"})
    st.caption(f"{len(show):,}개 종목 · '분봉 서버끝'이 체크된 종목은 서버가 가진 가장 오래된 분봉까지 이미 받은 것입니다.")
    st.dataframe(show, use_container_width=True, hide_index=True, height=420)

# ===========================================================================
# 탭 2: 수집 실행
# ===========================================================================
with tab_run:
    if active_job is not None:
        st.info(f"작업 #{active_job['id']}이(가) 진행 중입니다. 키움 호출 제한 때문에 수집 작업은 한 번에 하나만 실행할 수 있습니다.")

    st.markdown("##### 1. 대상 종목")
    mode = st.radio("어떤 종목을 받을까요?", [
        "카탈로그가 찾은 누락·부족 종목 (권장)", "관심종목만", "전체 종목 (universe)", "직접 선택"],
        key="run_mode")

    target_symbols: list[str] = []
    if mode.startswith("카탈로그"):
        gaps_run = find_gaps(store, min_minute_days=60, matrix=matrix)
        picked = st.multiselect("포함할 이유", list(REASON_LABELS.values()),
                                default=list(REASON_LABELS.values()), key="run_reasons")
        picked_keys = {k for k, v in REASON_LABELS.items() if v in picked}
        target_symbols = gaps_run[gaps_run["reasons"].map(lambda r: bool(picked_keys & set(r)))]["symbol"].tolist()
        st.caption("분봉 '얕음'은 60거래일 미만 기준입니다. (현황 탭에서 기준을 바꿔볼 수 있지만 수집 대상은 60일 고정)")
    elif mode == "관심종목만":
        target_symbols = store.get_watchlist()
    elif mode.startswith("전체"):
        target_symbols = store.universe_codes()
    else:
        chosen = st.multiselect("종목 선택", matrix["symbol"].tolist(), format_func=_label, key="run_pick")
        typed = st.text_input("또는 코드를 직접 입력 (공백/쉼표 구분)", key="run_typed",
                              placeholder="예: 005930 000660")
        target_symbols = list(dict.fromkeys(chosen + [c for c in typed.replace(",", " ").split() if c]))

    limit = st.number_input("최대 종목 수 (0 = 제한 없음)", min_value=0, value=0, step=50, key="run_limit",
                            help="관심종목 → 시가총액 큰 순으로 앞에서부터 자릅니다. 처음엔 작게 시험해보세요.")

    st.markdown("##### 2. 받을 데이터")
    d1, d2, d3, d4 = st.columns(4)
    want_daily = d1.checkbox("일봉", value=True, key="run_daily")
    want_minute = d2.checkbox("1분봉", value=True, key="run_minute")
    want_tick = d3.checkbox("틱봉", value=False, key="run_tick",
                            help="한 번에 받을 수 있는 분량이 짧아 최근 몇 분~몇 시간치만 채워집니다 (과거 확장 불가).")
    want_info = d4.checkbox("기본정보", value=True, key="run_info")

    depth_label = st.select_slider("분봉을 얼마나 과거까지?", options=list(_MINUTE_DEPTH_CHOICES),
                                   value="60일", disabled=not want_minute, key="run_depth")
    full_daily = st.checkbox("일봉 전체 재수집 (수정주가 반영 전 데이터를 덮어쓸 때만)", value=False, key="run_full")

    datasets = [name for name, on in (("daily", want_daily), ("minute", want_minute),
                                       ("tick", want_tick), ("info", want_info)) if on]
    opts = CollectOptions(
        datasets=datasets, full=full_daily,
        minute_target_days=_MINUTE_DEPTH_CHOICES[depth_label] if want_minute else 0,
        minute_scope=str(intraday_cfg.get("minute_scope", "1")),
        tick_scope=str(intraday_cfg.get("tick_scope", "1")),
        sleep_sec=config.get("data", {}).get("rate_limit_sleep_sec", 0.3),
    )
    if limit:
        # 미리보기와 실제 작업이 같은 종목을 자르도록 create_and_launch와 같은 정렬을 씁니다
        target_symbols = prioritize(store, target_symbols)[:int(limit)]

    st.markdown("##### 3. 확인 후 시작")
    if not datasets:
        st.warning("받을 데이터를 하나 이상 선택하세요.")
    elif not target_symbols:
        st.success("선택한 조건에 해당하는 종목이 없습니다 (이미 모두 최신입니다).")
    else:
        est = _estimate_seconds(target_symbols, opts)
        st.info(f"**{len(target_symbols):,}종목** · {' + '.join(DATASET_LABELS[d] for d in datasets)}"
                f" · 예상 소요 **약 {_fmt_seconds(est)}** (키움 호출 제한 기준 어림값). "
                f"중간에 멈춰도 받은 데이터는 저장되고, 다시 실행하면 이어서 진행됩니다.")
        if est > 6 * 3600:
            st.warning("6시간 넘게 걸리는 작업입니다. 관심종목/시가총액 순으로 처리하므로 중요한 종목부터 끝나지만, "
                       "처음에는 '최대 종목 수'를 줄여 시험해보길 권장합니다.")
        if st.button("▶ 수집 시작", type="primary", disabled=active_job is not None, key="run_start"):
            try:
                job_id = create_and_launch(store, target_symbols, opts)
                st.success(f"작업 #{job_id}을(를) 시작했습니다. 이 화면을 닫아도 계속 진행됩니다.")
                st.rerun()
            except JobAlreadyRunning as e:
                st.error(str(e))

    st.markdown("---")
    st.markdown("##### 실시간 데이터 이어붙이기")
    st.caption("실시간 수집기가 쌓은 체결 틱을 종목별 1분봉으로 만들어, 공식 분봉의 **빈 구간에만** 이어붙입니다. "
               "공식 분봉이 이미 있는 분은 덮어쓰지 않고, 나중에 공식 분봉을 받으면 자연스럽게 공식 값으로 교체됩니다. "
               "이미 합친 날짜/종목은 자동으로 건너뜁니다.")
    pending_rows = []
    for d in store.realtime_tick_dates():
        tick_counts = store.realtime_tick_counts(d)
        merged = store.merged_realtime_symbols(d)
        pending = [s for s, n in tick_counts.items() if merged.get(s) != n]
        pending_rows.append({"날짜": d, "실시간 종목 수": len(tick_counts), "병합 완료": len(tick_counts) - len(pending),
                             "병합 필요": len(pending)})
    if pending_rows:
        st.dataframe(pd.DataFrame(pending_rows), hide_index=True, use_container_width=True)
    else:
        st.caption("아직 실시간 체결 데이터가 없습니다 (`run.bat realtime --watchlist`).")
    if st.button("🔗 지금 병합", key="merge_now", disabled=not any(r["병합 필요"] for r in pending_rows)):
        with st.spinner("병합 중..."):
            res = merge_realtime(store)
        st.success(f"{res['symbols']}건 병합 · 분봉 {res['bars_added']:,}개 추가 · 근사 일봉 {res['daily_filled']}개")
        st.rerun()

# ===========================================================================
# 탭 3: 관심종목 · 전략
# ===========================================================================
with tab_watch:
    st.caption("여기 있는 종목이 **실시간 매매(live_trade.py)** 와 **실시간 수집(stream_collector.py --watchlist)** 의 대상입니다. "
               "라이브 매매는 매 확인 주기마다 이 목록을 다시 읽으므로 바꾸면 곧바로 반영되지만, "
               "실시간 수집기는 시작할 때 한 번만 읽으므로 새 종목을 구독하려면 재시작이 필요합니다.")
    watch_df = store.get_watchlist_detail()
    st.markdown(f"##### 관심종목 {len(watch_df)}개")

    if watch_df.empty:
        st.info("관심종목이 비어 있습니다. 아래에서 추가하세요.")
    else:
        edit = watch_df.copy()
        edit.insert(1, "종목명", edit["symbol"].map(lambda s: names.get(s, "")))
        edit["제거"] = False
        source_labels = {"manual": "직접 추가", "position": "보유 종목", "strategy": "전략 후보", "seed": "최초 자동"}
        edit["source"] = edit["source"].map(lambda s: source_labels.get(s, s))
        edited = st.data_editor(
            edit[["symbol", "종목명", "source", "strategy", "note", "제거"]],
            hide_index=True, use_container_width=True, key="watch_editor",
            disabled=["symbol", "종목명", "source"],
            column_config={
                "symbol": "코드", "source": "출처",
                "strategy": st.column_config.TextColumn(
                    "전략 그룹", help="차트 화면 사이드바에서 이 이름으로 묶어 보여줍니다 (예: 단타, 스윙). "
                                  "비워두면 그 종목에 적용되는 전략 이름(config)으로 자동 분류됩니다."),
                "note": "메모",
                "제거": st.column_config.CheckboxColumn("관심 해제"),
            })
        if st.button("💾 변경 저장", key="watch_save"):
            original = watch_df.set_index("symbol")
            changed = 0
            for row in edited.itertuples(index=False):
                sym = row.symbol
                if row.제거:
                    store.remove_from_watchlist(sym)
                    changed += 1
                    continue
                new_strategy, new_note = (row.strategy or "").strip(), (row.note or "").strip()
                if new_strategy != (original.loc[sym, "strategy"] or "") or new_note != (original.loc[sym, "note"] or ""):
                    store.set_watch_tags(sym, strategy=new_strategy, note=new_note)
                    changed += 1
            st.success(f"{changed}개 종목을 갱신했습니다.")
            st.rerun()

    st.markdown("##### 종목 추가")
    a1, a2, a3 = st.columns([3, 2, 1])
    add_pick = a1.multiselect("종목", matrix["symbol"].tolist(), format_func=_label, key="watch_add_pick")
    add_strategy = a2.text_input("전략 그룹 (선택)", key="watch_add_strategy", placeholder="예: 단타")
    if a3.button("＋ 추가", key="watch_add_btn", disabled=not add_pick):
        for s in add_pick:
            store.add_to_watchlist(s, source="manual", strategy=add_strategy.strip())
        st.rerun()

    st.markdown("##### 전략 신호로 후보 찾기")
    st.caption("저장된 일봉에 config의 기본 전략을 적용해 지금 BUY 신호인 종목을 찾습니다 "
               "(어제 종가 기준, 후보 발굴용 — 실제 주문 판단은 live_trade.py가 실시간가로 다시 합니다).")
    scan_scope = st.radio("스캔 범위", ["관심종목", "전체 종목 (수 초~수십 초)"], horizontal=True, key="scan_scope")
    if st.button("🔍 신호 스캔", key="scan_btn"):
        scope_symbols = store.get_watchlist() if scan_scope == "관심종목" else store.universe_codes()
        with st.spinner(f"{len(scope_symbols):,}종목 스캔 중..."):
            st.session_state["scan_result"] = scan_signals(store, config, scope_symbols)
    scan = st.session_state.get("scan_result")
    if scan is not None:
        if scan.empty:
            st.caption("스캔 결과가 없습니다.")
        else:
            sig_counts = scan["signal"].value_counts()
            st.caption(" · ".join(f"{k} {v:,}" for k, v in sig_counts.items()))
            buys = scan[scan["signal"] == "BUY"].copy()
            if buys.empty:
                st.info("지금 BUY 신호인 종목이 없습니다.")
            else:
                buys["종목명"] = buys["symbol"].map(lambda s: names.get(s, ""))
                st.dataframe(buys[["symbol", "종목명", "last_date", "last_close"]].rename(
                    columns={"symbol": "코드", "last_date": "기준일", "last_close": "종가"}),
                    hide_index=True, use_container_width=True)
                already = set(store.get_watchlist())
                addable = [s for s in buys["symbol"] if s not in already]
                cand_pick = st.multiselect("관심종목에 추가할 후보", addable, default=addable[:5],
                                           format_func=_label, key="scan_pick")
                if st.button("＋ 전략 후보로 추가", key="scan_add", disabled=not cand_pick):
                    strat_name = config.get("strategy", {}).get("name", "")
                    for s in cand_pick:
                        store.add_to_watchlist(s, source="strategy", strategy=strat_name)
                    st.success(f"{len(cand_pick)}종목을 전략 후보로 추가했습니다.")
                    st.rerun()

# ===========================================================================
# 탭 4: 작업 이력
# ===========================================================================
with tab_history:
    jobs = store.recent_jobs(30)
    if jobs.empty:
        st.caption("아직 수집 작업 기록이 없습니다.")
    else:
        show_jobs = jobs[["id", "created_at", "status", "title", "total", "done", "ok", "failed", "message"]].copy()
        show_jobs["status"] = show_jobs["status"].map(lambda s: _STATUS_LABEL.get(s, s))
        st.dataframe(show_jobs.rename(columns={
            "id": "#", "created_at": "생성", "status": "상태", "title": "내용", "total": "대상",
            "done": "처리", "ok": "성공", "failed": "실패", "message": "마지막 메시지"}),
            hide_index=True, use_container_width=True)

        pick_id = st.selectbox("작업 상세", jobs["id"].tolist(), key="history_job")
        failed_items = store.job_items(int(pick_id), status="failed")
        if failed_items.empty:
            st.caption("이 작업에는 실패한 종목이 없습니다.")
        else:
            st.markdown(f"**실패 {len(failed_items)}종목**")
            st.dataframe(failed_items.rename(columns={"symbol": "코드", "status": "상태", "detail": "사유",
                                                       "finished_at": "시각"}),
                         hide_index=True, use_container_width=True)
            if st.button("↻ 실패 종목만 다시 수집", key="retry_failed", disabled=active_job is not None):
                spec = store.get_job(int(pick_id))["spec"]
                try:
                    new_id = create_and_launch(store, failed_items["symbol"].tolist(),
                                               CollectOptions.from_dict(spec.get("options", {})),
                                               title=f"작업 #{pick_id} 실패 {len(failed_items)}종목 재시도")
                    st.success(f"작업 #{new_id}을(를) 시작했습니다.")
                    st.rerun()
                except JobAlreadyRunning as e:
                    st.error(str(e))
