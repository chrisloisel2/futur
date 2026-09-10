# Preregistration — `microstructure_imbalance_v1`

Written before the rule was run. The decision-relevant fields are in
`spec.json` and their SHA-256 is the `rules_hash`.

## The claim

On Binance USDT-M perpetuals, when the notional resting at the best bid exceeds
the notional at the best ask by an unusual margin, the mid price drifts toward
the heavy side over the next sixty seconds by more than the round trip paid to
take it.

## Why it should exist

Displayed size at the touch is the visible part of an imbalance that has to
clear. A maker facing a one-sided queue is exposed to being run over, so they
widen or step away on that side and the mid moves before the imbalance
resolves. Whoever takes the other side is paid for the risk the maker is
refusing. The payment decays in seconds, which is why slower capital does not
compete it away.

## The rule

On a one-second grid, per symbol, compute the notional imbalance at the touch
and its trailing five-minute z-score, strictly causal. When the absolute
z-score reaches three and the spread is at or below one basis point, take the
heavy side and exit sixty seconds later at the mid. No stop, no filter, no
discretion.

## One rule, one trial

No sensitivities are declared. Changing the threshold, the window or the
horizon produces a different hash and costs another trial in the
`microstructure` family.

## Declustering, chosen before the result

`complete_link`, five minutes, per symbol. Five minutes is five times the
horizon, so two entries inside one window are one bet, not two.

## What this test can and cannot decide

The capture is ten days on three symbols. Whatever the verdict says, the window
is short and the verdict must say so. What ten days can decide is the **cost
question**: whether the gross drift is even the same order of magnitude as the
round trip. That question does not need years.

## Execution assumption, stated plainly

Taker in and taker out at VIP0, which is about ten basis points of fees alone.
That is the only execution this repository can honestly simulate today, because
no maker fill rate has been measured and sealed. A maker-entry variant is a
**different hypothesis**: it needs the maker fill probe's measured fill rate
first, and it will carry its own hash and its own trial.

## What would kill it

Gross drift below three times the round trip. Failure against its own placebo
null. A t on the gross series below the `microstructure` family threshold.
Latency above fifteen seconds, which is the horizon over four.
