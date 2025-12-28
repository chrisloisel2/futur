# Exchange Outage Runbook

- Detect via ping/ws disconnect/outage_seconds.
- Actions: halt new orders, cancel live orders if possible, switch mode to OFF, notify on-call.
- Resume after connectivity restored and risk controller approval.
