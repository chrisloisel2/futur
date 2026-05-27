from pipeline.risk.var_cvar import VaREngine


def test_var_engine_methods():
    engine = VaREngine(method="parametric")
    assert engine.method == "parametric"
