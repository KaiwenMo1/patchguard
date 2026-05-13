def apply_discount(price: float, percent: float) -> float:
    """Apply a percentage discount and round to cents."""
    discounted = price * (1 - percent / 100)
    return int(discounted)
