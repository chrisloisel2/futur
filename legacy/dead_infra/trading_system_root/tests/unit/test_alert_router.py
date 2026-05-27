from pipeline.monitoring.alerts import AlertRouter


def test_alert_router_builds():
    router = AlertRouter({})
    reports = {"event_time": None, "perf_drift": {"by_symbol": {"BTC": {"severity": "CRIT"}}}}
    alerts = router.build_alerts(reports, type("ap", (), {"run_id": "r"})())
    assert alerts
