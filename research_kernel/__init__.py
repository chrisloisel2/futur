"""research_kernel — the only active research path of this repository.

Hard rules enforced by tests in ``research_kernel/tests``:

1. No module in this package may import from ``ai/``, ``trading-system/``,
   ``frontend_pipeline/``, ``signals/``, ``scrapers/``, ``legacy/``, ``core/``,
   ``risk/`` or ``src/futur``.  Reading data files those systems produced is
   allowed; importing their logic is not.
2. Every mechanism owns a directory under ``mechanisms/`` with a ``spec.json``.
3. Costs are applied before any result is looked at.
4. No status above ``PROMISING_NEEDS_FORWARD`` without a sealed forward window.

The package produces verdicts, not backtests.
"""

__all__ = [
    "cost_model",
    "data_contracts",
    "declustering",
    "forward_seal",
    "ledger",
    "mechanism_spec",
    "multiplicity",
    "placebo",
    "report",
    "validation",
    "verdict",
]

KERNEL_VERSION = "0.1.0"
