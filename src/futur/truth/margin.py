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
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarginConfig:
    initial_margin_rate: float = 0.10
    maintenance_margin_rate: float = 0.05

    def __post_init__(self) -> None:
        if not (0.0 < self.maintenance_margin_rate <= self.initial_margin_rate):
            raise ValueError(
                "maintenance_margin_rate must be in (0, initial_margin_rate], got "
                f"maintenance={self.maintenance_margin_rate!r} "
                f"initial={self.initial_margin_rate!r}"
            )


@dataclass(frozen=True)
class Exposures:
    spot_gross: float
    perp_gross: float
    total_gross: float
    net_exposure: float
    long_exposure: float
    short_exposure: float
    exposure_by_asset: dict = field(default_factory=dict)     # symbol -> gross exposure
    exposure_by_venue: dict = field(default_factory=dict)      # venue -> gross exposure


@dataclass(frozen=True)
class MarginState:
    perp_notional: float
    initial_margin_required: float
    maintenance_margin_required: float
    collateral_equity: float
    margin_available: float


def _price_for_exposure(account, key: str, fallback_price: float) -> float:
    return account.marks.get(key, fallback_price)


def compute_exposures(account) -> Exposures:
    long_exposure = 0.0
    short_exposure = 0.0
    by_asset: dict = {}
    by_venue: dict = {}
    spot_gross = 0.0
    perp_gross = 0.0

    for key, pos in account.spot_positions.items():
        price = _price_for_exposure(account, key, pos.last_price)
        value = pos.quantity * price
        spot_gross += abs(value)
        if value > 0:
            long_exposure += value
        elif value < 0:
            short_exposure += abs(value)
        by_asset[pos.instrument.symbol] = by_asset.get(pos.instrument.symbol, 0.0) + abs(value)
        by_venue[pos.instrument.venue] = by_venue.get(pos.instrument.venue, 0.0) + abs(value)

    for key, pos in account.perp_positions.items():
        price = _price_for_exposure(account, key, pos.avg_entry_price)
        value = pos.quantity * price
        perp_gross += abs(value)
        if value > 0:
            long_exposure += value
        elif value < 0:
            short_exposure += abs(value)
        by_asset[pos.instrument.symbol] = by_asset.get(pos.instrument.symbol, 0.0) + abs(value)
        by_venue[pos.instrument.venue] = by_venue.get(pos.instrument.venue, 0.0) + abs(value)

    total_gross = spot_gross + perp_gross
    return Exposures(
        spot_gross=spot_gross, perp_gross=perp_gross, total_gross=total_gross,
        net_exposure=long_exposure - short_exposure,
        long_exposure=long_exposure, short_exposure=short_exposure,
        exposure_by_asset=by_asset, exposure_by_venue=by_venue,
    )


def compute_margin_state(account, config: MarginConfig) -> MarginState:
    perp_notional = 0.0
    for key, pos in account.perp_positions.items():
        price = _price_for_exposure(account, key, pos.avg_entry_price)
        perp_notional += abs(pos.quantity) * price

    initial_margin_required = perp_notional * config.initial_margin_rate
    maintenance_margin_required = perp_notional * config.maintenance_margin_rate
    collateral_equity = account.nav()
    margin_available = collateral_equity - initial_margin_required
    return MarginState(
        perp_notional=perp_notional,
        initial_margin_required=initial_margin_required,
        maintenance_margin_required=maintenance_margin_required,
        collateral_equity=collateral_equity,
        margin_available=margin_available,
    )


def can_open_additional_notional(account, config: MarginConfig,
                                  additional_notional: float) -> bool:
    """Would opening `additional_notional` of new perp exposure (in
    account.base_currency) leave the account with sufficient initial
    margin? Pure question, no side effect -- the caller decides what to do
    with the answer (accept/reject an order)."""
    current = compute_margin_state(account, config)
    projected_additional_im = abs(additional_notional) * config.initial_margin_rate
    projected_available = current.collateral_equity - (
        current.initial_margin_required + projected_additional_im)
    return projected_available >= 0.0


def should_liquidate(account, config: MarginConfig) -> bool:
    state = compute_margin_state(account, config)
    if state.maintenance_margin_required <= 0.0:
        return False    # no perp exposure at all -- nothing to liquidate
    return state.collateral_equity < state.maintenance_margin_required
