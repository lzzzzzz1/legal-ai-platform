import json
from types import SimpleNamespace

from app.services import contract_overview


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeClient:
    def __init__(self, *contents: str) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(contents[0]))


def test_overview_fallback_is_safe_when_model_call_fails(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "configured-for-test")

    def failing_client(**_: object) -> object:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(contract_overview, "OpenAI", failing_client)

    overview = contract_overview.create_contract_overview("甲方委托乙方提供服务。")

    assert overview.method == "fallback"
    assert len(overview.dimensions) == 7
    assert overview.decision_points
    assert "模型连接异常" in overview.warnings[0]


def test_overview_uses_model_when_json_matches_schema(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "configured-for-test")
    payload = {
        "contract_type": "服务合同",
        "summary": "甲方采购乙方服务。",
        "parties": ["甲方", "乙方"],
        "transaction_subject": "软件服务",
        "key_terms": ["验收后付款"],
        "dimensions": [
            {"category": "交易结构", "status": "stated", "details": ["甲方采购服务"]}
        ],
        "business_flow": ["乙方服务", "甲方验收并付款"],
        "party_responsibilities": [{"party": "乙方", "responsibilities": ["提供服务"]}],
        "decision_points": [{"topic": "验收标准", "contract_position": "已有验收约定", "user_question": "确认验收口径"}],
        "clarification_questions": [],
    }
    monkeypatch.setattr(contract_overview, "OpenAI", lambda **_: _FakeClient(json.dumps(payload)))

    overview = contract_overview.create_contract_overview("甲方采购乙方软件服务，验收后付款。")

    assert overview.method == "model"
    assert overview.summary == "甲方采购乙方服务。"
    assert overview.warnings == []


def test_overview_retries_when_gateway_returns_a_top_level_array(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "configured-for-test")
    responses = iter([
        "[]",
        json.dumps({
            "contract_type": "服务合同",
            "summary": "甲方采购乙方服务。",
            "parties": ["甲方", "乙方"],
            "transaction_subject": "软件服务",
            "key_terms": [],
            "dimensions": [],
            "business_flow": ["乙方提供服务"],
            "party_responsibilities": [],
            "decision_points": [],
            "clarification_questions": [],
        }),
    ])

    class RetryCompletions:
        def create(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))])

    class RetryClient:
        chat = SimpleNamespace(completions=RetryCompletions())

    monkeypatch.setattr(contract_overview, "OpenAI", lambda **_: RetryClient())
    overview = contract_overview.create_contract_overview("甲方采购乙方服务。")

    assert overview.method == "model"
    assert overview.summary == "甲方采购乙方服务。"
