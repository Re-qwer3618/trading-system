"""
[3단계 예정] LLM을 보조 시그널로 연결하는 자리입니다.
지금은 항상 중립(NEUTRAL) 응답만 하는 빈 껍데기입니다.
config의 llm.enabled를 true로 바꾸고, 이미 만들어두신
gemma-2-9b(GGUF) 모델을 로드하는 로직을 이 안에 채워넣으면
전략 로직 변경 없이 LLM 자문이 매매 판단에 반영됩니다.
"""

from abc import ABC, abstractmethod


class BaseLLMAdvisor(ABC):
    @abstractmethod
    def get_opinion(self, symbol: str, context: str) -> dict:
        """
        반환 예시: {"stance": "NEUTRAL"|"POSITIVE"|"NEGATIVE", "reasoning": str}
        전략은 이 opinion을 '보조 참고'로만 쓰고, 최종 결정은 규칙기반 로직이 합니다.
        """
        raise NotImplementedError


class NoOpAdvisor(BaseLLMAdvisor):
    """LLM이 아직 연결되지 않았을 때 쓰는 기본값."""

    def get_opinion(self, symbol: str, context: str) -> dict:
        return {"stance": "NEUTRAL", "reasoning": "LLM 미연결 (3단계에서 구현 예정)"}


class LocalGemmaAdvisor(BaseLLMAdvisor):
    """
    [향후 구현] 이미 파인튜닝해두신 gemma-2-9b(Q4_K_M.gguf)를
    llama.cpp/ollama 등으로 로드해서 여기서 호출하도록 채워주세요.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        raise NotImplementedError("로컬 LLM 연동은 3단계에서 구현 예정입니다.")

    def get_opinion(self, symbol: str, context: str) -> dict:
        raise NotImplementedError
