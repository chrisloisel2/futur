from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal

BookId = Literal["book_a", "book_b", "book_c"]


@dataclass
class BookBudgetState:
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    leverage: float = 0.0
    dd: float = 0.0
    var: float = 0.0
    cvar: float = 0.0
    budget_remaining: float = 0.0


@dataclass
class BooksState:
    books: Dict[BookId, BookBudgetState] = field(default_factory=dict)
    active_books: List[BookId] = field(default_factory=list)
    last_rebalance_time: object | None = None
