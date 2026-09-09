# Preregistration — `cross_exchange_dislocation_v1`

Written before the rule was run. Decision-relevant fields are in `spec.json`,
hashed into `rules_hash`.

## The claim

When the Binance and OKX perpetual mid prices for the same symbol diverge by an
unusual amount, the gap closes within sixty seconds by more than the round trip
paid on **both** legs.

## Why it should exist

Two venues quote the same risk. A gap between them is a local liquidity shock
on one of the two, not a change in what the asset is worth, so inventory held
on both venues pulls it back. The compensation is for supplying that inventory
across a fragmented market, and it persists only because margin sits on one
venue at a time and cannot be moved instantly.

## The rule

On a one-second grid, align the two captures within one second and compute the
dislocation in basis points of the Binance mid. Take its trailing five-minute
z-score, strictly causal. When the absolute z-score reaches three, the absolute
dislocation reaches two basis points and both spreads are at or below two basis
points, buy the cheap venue and sell the rich one. Exit when half the gap has
closed or after sixty seconds, whichever comes first.

## Both legs are priced

The cost model declares `n_legs: 2`. A cross-venue result priced on one leg is
not a result. No rebate, no cross-margin and no netting between venues is
assumed, because none is available on a retail account.

## A data limit stated up front

Displayed quantities are not comparable across the two venues: Binance quotes
base units and OKX quotes contracts. This mechanism therefore uses mid prices
only. Anything about size, depth or capacity is out of its scope until a
contract multiplier table exists, and no capacity claim may be made from this
test.

## Declustering, chosen before the result

`complete_link`, five minutes, per symbol. A dislocation that persists is one
event, not one event per second.

## What would kill it

A gross capture below three times the twenty-four basis points of round trip on
two legs. Failure against its own placebo null. A t on the gross series below
the `cross_exchange` family threshold. Any evidence that the two captures are
not clock-aligned within the declared skew.
