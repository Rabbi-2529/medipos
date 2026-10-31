from decimal import Decimal

TAKA = "৳"


def format_taka(value):
    """৳1,250 or ৳1,250.50 — decimals only when there are paisa."""
    if value is None or value == "":
        return ""
    amount = Decimal(value)
    text = f"{amount:,.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    return f"{TAKA}{text}"
