import sys
from pathlib import Path


def pytest_configure() -> None:  # pragma: no cover - pytest hook
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
