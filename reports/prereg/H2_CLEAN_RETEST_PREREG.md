# H2 clean retest — preregistration DRAFT (not in force)

> **This document is not a preregistration.** It is a draft that becomes signable only when the P10 gate opens.
> The gate is currently **closed**: 0 clean events against a threshold of 80,
> and execution cost is unknown. Freeze `4f077025c30f6bfd…`, commit `1f55ec3f03b2`. Budget is 0 and none is requested here.
> Nothing in this file has been run, and no number in it comes from looking at a return.

## Why a retest would even be considered

Regard seq 8 left `event_listing_perp_fade_v1` INDECIDABLE: +251 bps mean, +230 median, t = 2.12 against a
family threshold of 2.3263, with a per-event dispersion of 1 546 bps. The result was not rejected for lack of
size; it was undecidable for lack of precision. Three things have changed since, none of them a look:

- the state around each launch exists (P6: mark, index, premium, trades, open interest, funding, depth);
- the event has a corroborated time (P7: announced opening time in the body, agreeing with the first traded bar
  within 5 minutes for 162 of 174);
- the population is describable (P9: 123 of the launches were already priced
  elsewhere; only 6 are first listings anywhere).

## The population question this draft must answer before it can be signed

A retest that pools both populations repeats the error of seq 8 under better lighting. Two honest options:

- **Test the genuine first listings only.** 6 events. This is far too
  few to decide anything at the observed dispersion; the minimum detectable effect would be several hundred bps.
  Preregistering it would be preregistering an INDECIDABLE.
- **Test the already-priced population** (123 events) as what it actually is: not
  a listing effect but a *venue-arrival* effect, with the pre-existing price on the other venue as the reference.
  That is a different mechanism and needs its own economic reason, not a recycled one.

Choosing between them is a decision about what is being claimed, and it is not made here.

## What a signed version would have to fix in the harness, before any look

1. Entry time from the corroborated opening time (P7), not from the first Vision bar alone.
2. The excess measured against the **index** (the venue's own composite of other venues) instead of BTCUSDT —
   the data exists now, and BTC was only ever a stand-in for a reference that was missing.
3. Cost from the **actual** account fee plus spread and slippage derived from the depth archives, not declared.
4. One primary horizon and at most two sensitivities, direction fixed in advance, family multiplicity charged
   at `threshold_t(n)` for the `news` family, which already carries 5 sealed hypotheses.
5. Events whose announced time and first bar disagree by more than 15 minutes excluded by rule, not by hand.

## What is forbidden regardless

Re-reading the seq 8 result, re-testing the same events at another horizon, choosing the population after seeing
returns, or promoting anything from a first look. A preregistration is signed before the data is touched, pushed
before the look, and consumes budget.
