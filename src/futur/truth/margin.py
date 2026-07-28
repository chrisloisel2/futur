"""src/futur/truth/margin.py -- exposures, margin, and liquidation checks.

Every function here is PURE: it reads `account.spot_positions`,
`account.perp_positions`, and `account.marks` fresh each call and returns a
new result -- there is no separate exposure/margin state stored on Account
that could independently drift from the positions it's supposed to
describe ("Toutes les expositions doivent être calculées à partir des
positions et marks actuels, jamais depuis des variables mises à jour
indépendamment").

Pricing fallback: exposure/margin sizing uses the current mark if one
exists, else the position's last known transaction price (avg_entry_price
for perp, last fill price for spot -- SpotPosition.last_price). Unlike
Account.spot_market_value()/perp_unrealized_pnl() (which treat "never
marked" as contributing 0 to NAV -- a reasonable choice there, since an
unmarked position hasn't been priced for P&L purposes yet), a margin
calculation that silently treated unmarked risk as zero would be a real
safety hole: it would let a position accumulate leverage invisibly just
because no MARK event happened to arrive yet. Falling back to the last
transaction price is conservative-by-construction, not zero.

All values are Decimal; margin rates are Decimal too (constructed via
numeric.to_decimal so a plain float/str rate still converts safely).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.futur.truth.numeric import quantize_cash, to_decimal

ZERO = Decimal(0)


@dataclass(frozen=True)
class MarginConfig:
    initial_margin_rate: Decimal = Decimal("0.10")
    maintenance_margin_rate: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_margin_rate", to_decimal(self.initial_margin_rate))
        object.__setattr__(self, "maintenance_margin_rate",
                          to_decimal(self.maintenance_margin_rate))
        if not (0 < self.maintenance_margin_rate <= self.initial_margin_rate):
            raise ValueError(
                "maintenance_margin_rate must be in (0, initial_margin_rate], got "
                f"maintenance={self.maintenance_margin_rate!r} "
                f"initial={self.initial_margin_rate!r}"
            )


@dataclass(frozen=True)
class Exposures:
    spot_gross: Decimal
    perp_gross: Decimal
    total_gross: Decimal
    net_exposure: Decimal
    long_exposure: Decimal
    short_exposure: Decimal
    exposure_by_asset: dict = field(default_factory=dict)     # symbol -> gross exposure
    exposure_by_venue: dict = field(default_factory=dict)      # venue -> gross exposure


@dataclass(frozen=True)
class MarginState:
    perp_notional: Decimal
    initial_margin_required: Decimal
    maintenance_margin_required: Decimal
    collateral_equity: Decimal
    margin_available: Decimal


def _price_for_exposure(account, key: str, fallback_price: Decimal) -> Decimal:
    return account.marks.get(key, fallback_price)


def compute_exposures(account) -> Exposures:
    long_exposure = ZERO
    short_exposure = ZERO
    by_asset: dict = {}
    by_venue: dict = {}
    spot_gross = ZERO
    perp_gross = ZERO

    for key, pos in account.spot_positions.items():
        price = _price_for_exposure(account, key, pos.last_price)
        value = pos.quantity * price
        spot_gross += abs(value)
        if value > 0:
            long_exposure += value
        elif value < 0:
            short_exposure += abs(value)
        by_asset[pos.instrument.symbol] = by_asset.get(pos.instrument.symbol, ZERO) + abs(value)
        by_venue[pos.instrument.venue] = by_venue.get(pos.instrument.venue, ZERO) + abs(value)

    for key, pos in account.perp_positions.items():
        price = _price_for_exposure(account, key, pos.avg_entry_price)
        value = pos.quantity * price
        perp_gross += abs(value)
        if value > 0:
            long_exposure += value
        elif value < 0:
            short_exposure += abs(value)
        by_asset[pos.instrument.symbol] = by_asset.get(pos.instrument.symbol, ZERO) + abs(value)
        by_venue[pos.instrument.venue] = by_venue.get(pos.instrument.venue, ZERO) + abs(value)

    total_gross = spot_gross + perp_gross
    return Exposures(
        spot_gross=quantize_cash(spot_gross), perp_gross=quantize_cash(perp_gross),
        total_gross=quantize_cash(total_gross),
        net_exposure=quantize_cash(long_exposure - short_exposure),
        long_exposure=quantize_cash(long_exposure), short_exposure=quantize_cash(short_exposure),
        exposure_by_asset={k: quantize_cash(v) for k, v in by_asset.items()},
        exposure_by_venue={k: quantize_cash(v) for k, v in by_venue.items()},
    )


def compute_margin_state(account, config: MarginConfig) -> MarginState:
    perp_notional = ZERO
    for key, pos in account.perp_positions.items():
        price = _price_for_exposure(account, key, pos.avg_entry_price)
        perp_notional += abs(pos.quantity) * price
    perp_notional = quantize_cash(perp_notional)

    initial_margin_required = quantize_cash(perp_notional * config.initial_margin_rate)
    maintenance_margin_required = quantize_cash(perp_notional * config.maintenance_margin_rate)
    collateral_equity = account.nav()
    margin_available = quantize_cash(collateral_equity - initial_margin_required)
    return MarginState(
        perp_notional=perp_notional,
        initial_margin_required=initial_margin_required,
        maintenance_margin_required=maintenance_margin_required,
        collateral_equity=collateral_equity,
        margin_available=margin_available,
    )


def can_open_additional_notional(account, config: MarginConfig,
                                  additional_notional: Decimal) -> bool:
    """Would opening `additional_notional` of new perp exposure (in
    account.base_currency) leave the account with sufficient initial
    margin? Pure question, no side effect -- the caller decides what to do
    with the answer (accept/reject an order)."""
    additional_notional = to_decimal(additional_notional)
    current = compute_margin_state(account, config)
    projected_additional_im = quantize_cash(abs(additional_notional) * config.initial_margin_rate)
    projected_available = current.collateral_equity - (
        current.initial_margin_required + projected_additional_im)
    return projected_available >= 0


def should_liquidate(account, config: MarginConfig) -> bool:
    state = compute_margin_state(account, config)
    if state.maintenance_margin_required <= 0:
        return False    # no perp exposure at all -- nothing to liquidate
    return state.collateral_equity < state.maintenance_margin_required
