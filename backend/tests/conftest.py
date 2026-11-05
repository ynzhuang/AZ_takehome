from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.data import load_dataset
from app.fx import FxTable
from app.llm import InterpreterError, ToolCall
from app.policy import load_policy
from app.tools import Context

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def stub_settings(**overrides) -> Settings:
    base = dict(
        data_dir=DATA_DIR, llm_provider="stub", llm_base_url="https://llm.invalid/v1", llm_api_key="",
        llm_model="test-model", llm_timeout_seconds=5.0, cors_origins=("http://localhost:5173",),
    )
    base.update(overrides)
    return Settings(**base)


class FakeInterpreter:
    """Returns a preset tool call (or raises), so service and API tests need no LLM."""

    label = "fake"

    def __init__(self, name: str = "decline", arguments: dict | None = None, error: InterpreterError | None = None):
        self.name, self.arguments, self.error = name, arguments or {}, error
        self.received_context = None

    def interpret(self, question: str, context=None) -> ToolCall:
        self.received_context = context
        if self.error:
            raise self.error
        return ToolCall(self.name, self.arguments, latency_ms=1)


@pytest.fixture(scope="session")
def fx() -> FxTable:
    return FxTable.from_ecb_csv(DATA_DIR / "fx" / "ecb_exr_2025.csv")


@pytest.fixture(scope="session")
def ctx(fx) -> Context:
    return Context(data=load_dataset(DATA_DIR, fx), policy=load_policy(DATA_DIR / "procurement_policy.md"), fx=fx)
