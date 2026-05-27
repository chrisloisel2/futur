import pandas as pd

from pipeline.monitoring import MonitoringPipeline


def test_monitoring_step_outputs():
    pipe = MonitoringPipeline({})
    out = pipe.step(pd.Timestamp.utcnow(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), run_id="run")
    assert "reports" in out
