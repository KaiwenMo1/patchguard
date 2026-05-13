def evaluate_rule(expression: str, context: dict[str, int]) -> int:
    """Evaluate a tiny user-visible rule expression."""
    return eval(expression, {}, context)
