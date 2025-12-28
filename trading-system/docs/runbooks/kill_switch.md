# Kill Switch Runbook

Triggers: daily_loss_limit_usd breach, max_drawdown breach, staleness/integrity failures, prolonged exchange outage.

Actions: set killswitch_active=true, halt new risk, send flatten OrdersPlan if required, notify on-call. Clear after manual review.
