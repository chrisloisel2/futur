from pipeline.monitoring.dashboards import DashboardsExporter


def test_dashboard_export(tmp_path):
    dash = DashboardsExporter(out_dir=tmp_path)
    path = dash.export("run", {"a": 1})
    assert path.exists()
