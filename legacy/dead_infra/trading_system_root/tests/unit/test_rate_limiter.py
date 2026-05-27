from infra.exchange.execution_client import ExecutionClient


class _Adapter:
    def place_order(self, intent):
        return intent
    def cancel_order(self, x):
        return {}
    def replace_order(self, a,b,c):
        return {}
    def fetch_open_orders(self):
        return []
    def fetch_fills(self, since=None):
        return []
    def ping(self):
        return True


def test_rate_limit_blocks():
    client = ExecutionClient(_Adapter(), rate_limit_tokens=0)
    try:
        client.place_order({})
        blocked = False
    except RuntimeError:
        blocked = True
    assert blocked
