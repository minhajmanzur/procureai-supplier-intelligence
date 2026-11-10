"""Number formatting shared by the analysis modules."""


def price(x: float) -> str:
    """Unit prices: cents-level parts (fasteners) need more than two decimals."""
    return f"{x:,.4f}" if abs(x) < 1 else f"{x:,.2f}"
