"""
백테스트로 "차트 분석가-1 (과거)"의 판단 기준(sma_short/sma_mid/sma_long)을
그리드서치하고, 성공/실패 + 성공<->실패 전환 이력을 DB(tuning_runs,
tuning_transitions 테이블)에 남깁니다.

실행:
    python src/tune_analysts.py 005930
    python src/tune_analysts.py 005930 --start 2025-01-01 --end 2025-09-01
    python src/tune_analysts.py 005930 --apply          # 1위 조합을 config/base.yaml에 바로 반영
    python src/tune_analysts.py 005930 --top 5          # 콘솔에 몇 개까지 보여줄지

주의: 지금은 history_analyst의 추세 파라미터만 튜닝합니다. realtime/comparison
위원은 과거 틱 데이터가 충분히 쌓이기 전까지는 백테스트 튜닝이 불가능합니다
(agents/comparison_analyst.py, tuning/analyst_tuner.py 상단 설명 참고).
"""

import sys
import argparse
import json
import logging
from pathlib import Path

from config_loader import load_config, CONFIG_DIR
from data_layer.storage import MarketDataStore
from tuning.analyst_tuner import HistoryAnalystTuner

logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="백테스트 기반 위원 파라미터 튜닝 (history_analyst)")
    parser.add_argument("symbol", nargs="?", default="005930")
    parser.add_argument("--start", default=None, help="백테스트 시작일 YYYY-MM-DD (기본: 저장된 전체 기간)")
    parser.add_argument("--end", default=None, help="백테스트 종료일 YYYY-MM-DD")
    parser.add_argument("--apply", action="store_true", help="1위 조합을 config/base.yaml에 바로 반영")
    parser.add_argument("--top", type=int, default=5, help="콘솔에 보여줄 상위 조합 개수")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()
    store = MarketDataStore(config["data"]["db_path"])
    tuner = HistoryAnalystTuner(store)

    log.info(f"[{args.symbol}] 그리드서치 시작 (기간: {args.start or '전체'} ~ {args.end or '전체'})")
    try:
        results = tuner.grid_search(args.symbol, start_date=args.start, end_date=args.end)
    except ValueError as e:
        log.error(str(e))
        return

    if not results:
        log.warning("유효한 조합이 없습니다 (데이터가 너무 적거나 모든 조합이 조건에 안 맞음).")
        return

    log.info(f"총 {len(results)}개 조합 평가 완료. 상위 {min(args.top, len(results))}개:")
    for rank, r in enumerate(results[: args.top], start=1):
        flip_note = f" (⚠️ 과거 {r['flip_count']}회 성공/실패 전환 이력 있음)" if r["flip_count"] else ""
        print(f"{rank}. {r['summary']}{flip_note}")

    print()
    print("전체 결과(JSON):")
    print(json.dumps(results[: args.top], ensure_ascii=False, indent=2, default=str))

    if args.apply:
        applied = tuner.apply_best(results, CONFIG_DIR / "base.yaml")
        if applied is None:
            log.warning("SUCCESS로 판정된 조합이 없어 config를 수정하지 않았습니다.")
        else:
            log.info(
                f"config/base.yaml의 analysts.history를 sma({applied['params']['sma_short']}/"
                f"{applied['params']['sma_mid']}/{applied['params']['sma_long']})로 갱신했습니다."
            )
    else:
        log.info("실제로 config에 반영하려면 --apply 옵션을 붙여서 다시 실행하세요.")


if __name__ == "__main__":
    main()
