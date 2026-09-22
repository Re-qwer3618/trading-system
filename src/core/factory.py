"""
config의 문자열 값(provider, strategy.name 등)을 보고
실제 어떤 클래스를 쓸지 결정하는 곳입니다.
새 부품(예: 키움 MCP)이 실제로 구현되면 여기 딕셔너리에 한 줄만 추가하면 됩니다.
"""

from data_layer.providers.dummy_provider import DummyProvider
from data_layer.providers.kiwoom_rest_provider import KiwoomRestProvider
from data_layer.providers.kiwoom_mcp_provider import KiwoomMCPProvider
from broker.paper_broker import PaperBroker
from broker.kiwoom_rest_broker import KiwoomRestBroker
from broker.kiwoom_mcp_broker import KiwoomMCPBroker
from strategy.ma_cross_strategy import MACrossStrategy
from llm.advisor import NoOpAdvisor, LocalGemmaAdvisor


def build_data_provider(config: dict):
    provider = config["data"]["provider"]
    if provider == "dummy":
        return DummyProvider()
    if provider == "kiwoom_rest":
        return KiwoomRestProvider(config)
    if provider == "kiwoom_mcp":
        return KiwoomMCPProvider(config)
    raise ValueError(f"알 수 없는 data provider: {provider}")


def build_broker(config: dict, data_store):
    provider = config["broker"]["provider"]
    if provider == "paper":
        state_path = f"{config['paths']['data_dir']}/paper_broker_state.json"
        return PaperBroker(data_store, state_path, config["broker"]["starting_cash"])
    if provider == "kiwoom_rest":
        return KiwoomRestBroker(config, data_store)
    if provider == "kiwoom_mcp":
        return KiwoomMCPBroker(config)
    raise ValueError(f"알 수 없는 broker provider: {provider}")


_STRATEGY_REGISTRY = {
    "ma_cross": MACrossStrategy,
}


def build_strategy(config: dict, symbol: str | None = None):
    """
    symbol을 주면 config["strategy"]["assignments"][symbol]을 먼저 찾고,
    없으면 기본 strategy.name/params로 폴백합니다.

    여러 종목에 서로 다른 전략을 붙이는 "전략 구성"의 1단계입니다.
    assignments가 비어있으면 지금까지처럼 전 종목이 같은 기본 전략을 씁니다
    (기존 호출부(main.py 등)는 수정 없이 그대로 동작).
    """
    assignments = config.get("strategy", {}).get("assignments", {}) or {}
    spec = assignments.get(symbol) if symbol else None

    if spec:
        name = spec["name"]
        params = spec.get("params", {})
    else:
        name = config["strategy"]["name"]
        params = config["strategy"].get("params", {})

    cls = _STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"알 수 없는 strategy: {name} (등록된 전략: {list(_STRATEGY_REGISTRY.keys())})")
    return cls(**params)


def build_llm_advisor(config: dict):
    if not config.get("llm", {}).get("enabled", False):
        return NoOpAdvisor()
    provider = config["llm"]["provider"]
    if provider == "local_gemma":
        return LocalGemmaAdvisor(config["llm"].get("model_path", ""))
    return NoOpAdvisor()
