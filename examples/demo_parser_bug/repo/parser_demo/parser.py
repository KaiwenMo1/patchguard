def parse_csv_line(text: str) -> list[str]:
    """Parse a tiny comma-separated line into trimmed fields."""
    if text == "":
        return [""]
    return [part.strip() for part in text.split(",")]
