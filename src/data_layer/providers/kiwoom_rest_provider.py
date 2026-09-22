"""
kiwoom-client 라이브러리(pip install kiwoom-client)로 키움 REST API에서
시세/종목 데이터를 받아 우리 시스템 공통 스키마로 변환합니다.

주의: 실제 API 응답의 정확한 컬럼명/메서드명은 이 파일을 처음 실행해서
inspect_kiwoom_*.py로 확인하기 전까지는 100% 확정이 아닙니다.
아래 _COLUMN_CANDIDATES / _METHOD_CANDIDATES에 자주 쓰이는 후보들을 넣어뒀고,
못 찾으면 실제로 뭐가 있는지 보여주는 에러를 던지도록 만들었습니다
(fetch_ohlcv는 이미 이 방식으로 검증됨 — inspect_kiwoom_chart.py 참고).

연속조회(페이지네이션)와 수정주가:
키움 차트 API(ka10081/80/79 등)는 한 번의 호출로 전체 과거 데이터를 주지 않고,
응답 헤더의 cont-yn/next-key로 이어받는 구조입니다(_paginate 참고). 이걸 안 쓰면
최근 한 페이지 분량만 쌓여서 장기 백테스트가 사실상 불가능합니다.
upd_stkpc_tp도 "1"(수정주가 적용)로 고정합니다 — "0"(비수정주가)으로 액면분할/
무상증자가 있었던 종목을 받으면 권리락 시점에 가짜 급등락 캔들이 생겨 전략이
오작동합니다.
"""

from datetime import datetime
import pandas as pd

from kiwoom_client import KiwoomAPI, extract_records, normalize, to_dataframe
from .base_provider import BaseDataProvider

# 키움 응답 컬럼명이 우리 스키마 중 무엇에 해당하는지 후보들.
# inspect_kiwoom_chart.py 실행 결과를 보고 실제 컬럼명으로 좁혀주세요.
_COLUMN_CANDIDATES = {
    "date": ["dt", "date", "base_dt", "stck_bsop_date"],
    "open": ["open_pric", "opn_prc", "stck_oprc", "open"],
    "high": ["high_pric", "hgh_prc", "stck_hgpr", "high"],
    "low": ["low_pric", "low_prc", "stck_lwpr", "low"],
    "close": ["close_pric", "cur_prc", "clos_prc", "stck_clpr", "close"],
    "volume": ["trde_qty", "trde_vol", "acml_vol", "volume"],
}

# 분봉/틱 차트는 날짜 대신 체결시각(YYYYMMDDHHmmss) 컬럼을 씁니다.
_INTRADAY_TS_CANDIDATES = ["cntr_tm", "cntr_time", "tm"]

# 업종(지수) 일봉(ka20006) 응답 컬럼. 공식 스펙 기준(spec_show ka20006).
_INDEX_COLUMN_CANDIDATES = {
    "date": ["dt"],
    "open": ["open_pric"],
    "high": ["high_pric"],
    "low": ["low_pric"],
    "close": ["cur_prc"],
    "volume": ["trde_qty"],
}

# 종목정보 리스트(ka10099) 응답 컬럼 후보. 공식 스펙 기준(spec_show ka10099)이며,
# 실제 kiwoom-client 응답에서 이름이 다르면 inspect_kiwoom_universe.py로 확인하세요.
_UNIVERSE_COLUMN_CANDIDATES = {
    "code": ["code", "stk_cd"],
    "name": ["name", "stk_nm"],
    "market_name": ["marketName", "market_name"],
    "list_count": ["listCount", "list_count"],
    "last_price": ["lastPrice", "last_price"],
    "listed_date": ["regDay", "reg_day"],
    "state": ["state"],
}

# api 객체에서 각 기능이 어느 네임스페이스/메서드에 달려있는지 후보들.
# fetch_ohlcv의 self.api.chart.stock_daily_chart는 이미 검증된 값이라 그대로 둡니다.
_MINUTE_METHOD_CANDIDATES = ["stock_minute_chart", "stock_min_chart", "minute_chart"]
_TICK_METHOD_CANDIDATES = ["stock_tick_chart", "tick_chart"]
_STKINFO_NAMESPACE_CANDIDATES = ["stkinfo", "stock_info", "stocks"]
_UNIVERSE_METHOD_CANDIDATES = ["info_list", "stock_info_list", "list"]


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise KeyError(
        f"다음 후보 중 일치하는 컬럼이 없습니다: {candidates}\n"
        f"실제 컬럼 목록: {list(df.columns)}\n"
        f"-> src/inspect_kiwoom_chart.py 실행 결과를 보고 kiwoom_rest_provider.py의 "
        f"해당 컬럼 후보 리스트를 실제 컬럼명으로 수정해주세요."
    )


def _to_int_volume(series: pd.Series) -> pd.Series:
    """거래량 컬럼을 int로. 일부 종목/봉에서 값이 None으로 오는 경우가 있어
    (실측 확인됨 — 분봉/틱에서 종목 100개 중 99개가 이걸로 수집 자체가 실패했었음)
    .astype(int)를 바로 쓰면 "int() argument ... not 'NoneType'"로 죽습니다.
    거래량이 없다는 뜻으로 보고 0으로 채웁니다."""
    return pd.to_numeric(series, errors="coerce").fillna(0).astype(int)


def _resolve_callable(namespace, candidates: list[str], what: str):
    for name in candidates:
        fn = getattr(namespace, name, None)
        if callable(fn):
            return fn
    available = [n for n in dir(namespace) if not n.startswith("_")]
    raise AttributeError(
        f"{what}: 다음 후보 메서드가 없습니다: {candidates}\n"
        f"이 네임스페이스에서 실제 사용 가능한 것들: {available}\n"
        f"-> src/inspect_kiwoom_universe.py 를 실행해서 정확한 이름을 확인한 뒤 "
        f"kiwoom_rest_provider.py의 후보 리스트를 수정해주세요."
    )


def _resolve_namespace(api, candidates: list[str], what: str):
    for name in candidates:
        ns = getattr(api, name, None)
        if ns is not None:
            return ns
    available = [n for n in dir(api) if not n.startswith("_")]
    raise AttributeError(
        f"{what}: 다음 후보 네임스페이스가 없습니다: {candidates}\n"
        f"api 객체에서 실제 사용 가능한 것들: {available}\n"
        f"-> src/inspect_kiwoom_universe.py 를 실행해서 정확한 이름을 확인한 뒤 "
        f"kiwoom_rest_provider.py의 후보 리스트를 수정해주세요."
    )


def _paginate(fn, kwargs: dict, date_field: str, stop_before: str | None, max_pages: int) -> list[dict]:
    """cont-yn/next-key 연속조회로 여러 페이지를 이어받아 레코드 리스트로 합칩니다.

    차트류 메서드(stock_daily_chart 등)는 전부 (cont_yn, next_key, **kwargs) 시그니처를
    쓰므로 이 헬퍼 하나로 일봉/분봉/틱/지수 차트를 모두 처리합니다.

    stop_before(YYYYMMDD)를 주면, 이미 저장소에 있는 구간(그 날짜 이하)에 도달한
    페이지에서 멈춥니다 — 매일 도는 증분 수집이 매번 전체 과거를 다시 훑지 않도록
    하는 최적화입니다. 처음 수집하는 종목(stop_before=None)은 max_pages까지 최대한 받습니다.
    """
    all_records: list[dict] = []
    cont_yn, next_key = "N", ""
    for _ in range(max_pages):
        resp = fn(cont_yn=cont_yn, next_key=next_key, **kwargs)
        _, records = extract_records(resp)
        if not records:
            break
        all_records.extend(records)

        if stop_before and any(str(r.get(date_field, ""))[:8] <= stop_before for r in records):
            break

        resp_cont = resp.get("cont-yn", "N")
        resp_next = resp.get("next-key", "")
        if resp_cont != "Y" or not resp_next:
            break
        cont_yn, next_key = "Y", resp_next

    return all_records


class KiwoomRestProvider(BaseDataProvider):
    def __init__(self, config: dict):
        self.api = KiwoomAPI(
            app_key=config["_secrets"]["kiwoom_app_key"],
            app_secret=config["_secrets"]["kiwoom_app_secret"],
            is_mock=config.get("kiwoom", {}).get("is_mock", True),
        )
        history_cfg = config.get("data", {}).get("history", {}) or {}
        # 일봉은 넉넉히(장기 백테스트용), 분봉/틱은 보수적으로(collect_all.py가
        # 전 종목을 순회할 때 API 호출이 페이지 수만큼 곱해지므로 과하게 늘리지 마세요).
        self.max_pages_daily = int(history_cfg.get("max_pages_daily", 20))
        self.max_pages_intraday = int(history_cfg.get("max_pages_intraday", 3))

    def fetch_ohlcv(self, symbol: str, start_date: str | None = None) -> pd.DataFrame:
        """일봉 (ka10081). cont-yn/next-key로 연속조회하고 수정주가를 적용합니다.

        start_date가 있으면(증분 수집) 그 날짜 이하 데이터가 나오는 페이지에서
        조기 종료합니다. 없으면(최초 수집) max_pages_daily까지 최대한 받습니다.
        """
        base_dt = datetime.now().strftime("%Y%m%d")
        stop_before = start_date.replace("-", "") if start_date else None
        records = _paginate(
            self.api.chart.stock_daily_chart,
            {
                "stk_cd": symbol,
                "base_dt": base_dt,
                "upd_stkpc_tp": "1",  # 수정주가구분: 1=수정주가 적용 (액면분할/무상증자 등 권리락 보정)
            },
            date_field="dt",
            stop_before=stop_before,
            max_pages=self.max_pages_daily,
        )
        df = to_dataframe(records)

        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        col = {key: _find_column(df, cands) for key, cands in _COLUMN_CANDIDATES.items()}

        out = pd.DataFrame({
            "date": pd.to_datetime(df[col["date"]].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d"),
            "open": df[col["open"]].astype(float),
            "high": df[col["high"]].astype(float),
            "low": df[col["low"]].astype(float),
            "close": df[col["close"]].astype(float),
            "volume": _to_int_volume(df[col["volume"]]),
        })
        out = out.sort_values("date")

        if start_date:
            out = out[out["date"] > start_date]

        return out.reset_index(drop=True)

    def fetch_index_ohlcv(self, index_code: str = "001", start_date: str | None = None) -> pd.DataFrame:
        """업종(시장) 지수 일봉 (ka20006). index_code: "001"=코스피 종합, "101"=코스닥 종합.

        종목 백테스트 결과를 시장 대비로 비교(알파 계산)하는 벤치마크 데이터용입니다.
        지수 값은 키움 응답에서 소수점이 제거된 100배 정수로 오므로 100으로 나눠 되돌립니다.
        """
        base_dt = datetime.now().strftime("%Y%m%d")
        stop_before = start_date.replace("-", "") if start_date else None
        records = _paginate(
            self.api.chart.industry_daily_chart,
            {"inds_cd": index_code, "base_dt": base_dt},
            date_field="dt",
            stop_before=stop_before,
            max_pages=self.max_pages_daily,
        )
        df = to_dataframe(records)

        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        col = {key: _find_column(df, cands) for key, cands in _INDEX_COLUMN_CANDIDATES.items()}

        out = pd.DataFrame({
            "date": pd.to_datetime(df[col["date"]].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d"),
            "open": df[col["open"]].astype(float).abs() / 100,
            "high": df[col["high"]].astype(float).abs() / 100,
            "low": df[col["low"]].astype(float).abs() / 100,
            "close": df[col["close"]].astype(float).abs() / 100,
            "volume": _to_int_volume(df[col["volume"]]),
        })
        out = out.sort_values("date")

        if start_date:
            out = out[out["date"] > start_date]

        return out.reset_index(drop=True)

    def _fetch_intraday(self, symbol: str, tic_scope: str, method_candidates: list[str],
                         what: str, base_dt: str | None = None) -> pd.DataFrame:
        """분봉(ka10080)/틱(ka10079) 공용 로직. 응답 형태가 일봉과 거의 같고
        날짜 대신 체결시각(cntr_tm, YYYYMMDDHHmmss)을 씁니다.

        max_pages_intraday까지 연속조회합니다 (일봉만큼 깊게 받지 않는 이유는
        __init__의 주석 참고). start_date 기반 조기종료는 아직 안 함 — collect_all.py가
        분봉/틱을 증분 없이 매번 새로 받는 기존 동작과 맞춥니다."""
        fn = _resolve_callable(self.api.chart, method_candidates, what)
        kwargs = {"stk_cd": symbol, "tic_scope": tic_scope, "upd_stkpc_tp": "1"}
        if base_dt is not None:
            kwargs["base_dt"] = base_dt
        records = _paginate(
            fn, kwargs, date_field="cntr_tm", stop_before=None, max_pages=self.max_pages_intraday
        )
        df = to_dataframe(records)

        if df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        ts_col = _find_column(df, _INTRADAY_TS_CANDIDATES)
        price_cols = {k: v for k, v in _COLUMN_CANDIDATES.items() if k != "date"}
        col = {key: _find_column(df, cands) for key, cands in price_cols.items()}

        out = pd.DataFrame({
            "timestamp": pd.to_datetime(df[ts_col].astype(str), format="%Y%m%d%H%M%S").dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": df[col["open"]].astype(float).abs(),
            "high": df[col["high"]].astype(float).abs(),
            "low": df[col["low"]].astype(float).abs(),
            "close": df[col["close"]].astype(float).abs(),
            "volume": _to_int_volume(df[col["volume"]]),
        })
        return out.sort_values("timestamp").reset_index(drop=True)

    def fetch_minute(self, symbol: str, minute_scope: str = "5", base_dt: str | None = None) -> pd.DataFrame:
        """분봉 (ka10080). minute_scope: 1/3/5/10/15/30/45/60(분)."""
        return self._fetch_intraday(symbol, minute_scope, _MINUTE_METHOD_CANDIDATES, "fetch_minute", base_dt)

    def fetch_tick(self, symbol: str, tick_scope: str = "1") -> pd.DataFrame:
        """틱차트 (ka10079). tick_scope: 1/3/5/10/30(틱)."""
        return self._fetch_intraday(symbol, tick_scope, _TICK_METHOD_CANDIDATES, "fetch_tick")

    def fetch_universe(self, market_codes: list[str] | None = None) -> pd.DataFrame:
        """전체 종목 리스트 (ka10099). market_codes 기본값: ["0","10"] (코스피/코스닥).
        반환 컬럼: code, market, name, market_name, list_count, last_price, listed_date, state
        """
        market_codes = market_codes or ["0", "10"]
        namespace = _resolve_namespace(self.api, _STKINFO_NAMESPACE_CANDIDATES, "fetch_universe(namespace)")
        fn = _resolve_callable(namespace, _UNIVERSE_METHOD_CANDIDATES, "fetch_universe(method)")

        frames = []
        for market in market_codes:
            raw = fn(mrkt_tp=market)
            df = to_dataframe(raw)
            if df.empty:
                continue
            col = {key: _find_column(df, cands) for key, cands in _UNIVERSE_COLUMN_CANDIDATES.items()}
            out = pd.DataFrame({
                "code": df[col["code"]].astype(str),
                "name": df[col["name"]].astype(str),
                "market_name": df[col["market_name"]].astype(str),
                "list_count": df[col["list_count"]].astype(str),
                "last_price": pd.to_numeric(df[col["last_price"]], errors="coerce").abs(),
                "listed_date": df[col["listed_date"]].astype(str),
                "state": df[col["state"]].astype(str),
            })
            out["market"] = market
            frames.append(out)

        if not frames:
            return pd.DataFrame(columns=["code", "market", "name", "market_name",
                                          "list_count", "last_price", "listed_date", "state"])
        return pd.concat(frames, ignore_index=True)

    def fetch_basic_info(self, symbol: str) -> dict:
        """종목 기본정보 (ka10001): PER/PBR/EPS/BPS/ROE, 시가총액, 매출액/영업이익/순이익 등.
        차트가 아니라 시점 스냅샷 하나라 연속조회가 필요 없습니다. PER/ROE 등은 외부
        벤더사 제공 데이터라 종목에 따라 비어있을 수 있습니다(공식 스펙에 명시된 내용)."""
        raw = self.api.stock_info.basic_stock_info(stk_cd=symbol)
        data = normalize(raw)
        return {
            "symbol": symbol,
            "name": data.get("stk_nm"),
            "market_cap": data.get("mac"),
            "per": data.get("per"),
            "pbr": data.get("pbr"),
            "eps": data.get("eps"),
            "bps": data.get("bps"),
            "roe": data.get("roe"),
            "sales": data.get("sale_amt"),
            "operating_profit": data.get("bus_pro"),
            "net_income": data.get("cup_nga"),
            "listed_shares": data.get("flo_stk"),
            # 스펙상 "부호가 포함된 숫자"인데, 부호는 기준가 대비 등락 방향 표시일 뿐
            # 실제로 250일 최저가가 음수일 수는 없어서 abs() 처리합니다 (실측 확인됨).
            "high_250": abs(data["250hgst"]) if data.get("250hgst") is not None else None,
            "low_250": abs(data["250lwst"]) if data.get("250lwst") is not None else None,
        }
