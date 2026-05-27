from domain.risk.budgets import RiskBudgets
from pipeline.meta_control.portfolio_router import PortfolioRouter, RouterConfig


def test_router_respects_cluster_caps():
    router = PortfolioRouter(RouterConfig(router_max_assets=2, asset_weight_cap=0.6))
    scores = {"BTC": 1.0, "ETH": 0.9, "SOL": 0.8}
    budgets = RiskBudgets(meta_cluster_caps={"default": 0.7}, meta_max_concentration=0.6)
    weights = router.allocate(scores, budgets, {"BTC": "default", "ETH": "default", "SOL": "default"})
    assert sum(weights.values()) <= 0.7
