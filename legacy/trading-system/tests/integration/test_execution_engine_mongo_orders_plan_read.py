from datetime import datetime

from pipeline.execution.engine import ExecutionEngine


def test_engine_instantiates():
    ExecutionEngine({})
    assert True
