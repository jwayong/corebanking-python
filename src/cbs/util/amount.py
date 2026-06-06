"""Amount and scale conversion helpers.

Mirrors the Go `tigerbeetleutil` package for amount scaling operations.
TigerBeetle stores all amounts as integers at a specific scale (minor units).
These helpers convert between different scales and human-readable decimal values.
"""

from __future__ import annotations


def scale_up(amount: int, from_scale: int, to_scale: int) -> int:
    """Convert an amount from a lower scale to a higher scale.

    Example:
        ``scale_up(100, 0, 2)`` returns ``10_000``
        (100 JPY whole units → 10,000 in 2-decimal minor units).

    Args:
        amount: The integer amount at *from_scale*.
        from_scale: Current scale (number of decimal places).
        to_scale: Target scale (must be >= *from_scale* for meaningful result).

    Returns:
        The amount scaled to *to_scale*. If *to_scale* <= *from_scale*,
        returns *amount* unchanged.
    """
    diff = to_scale - from_scale
    if diff <= 0:
        return amount
    return amount * (10 ** diff)


def scale_down(amount: int, from_scale: int, to_scale: int) -> int:
    """Convert an amount from a higher scale to a lower scale (truncates).

    Example:
        ``scale_down(10_050, 2, 0)`` returns ``100``
        (10,050 cents → 100 whole units).

    Args:
        amount: The integer amount at *from_scale*.
        from_scale: Current scale (number of decimal places).
        to_scale: Target scale (must be <= *from_scale* for meaningful result).

    Returns:
        The amount scaled to *to_scale*, truncated toward zero. If
        *from_scale* <= *to_scale*, returns *amount* unchanged.
    """
    diff = from_scale - to_scale
    if diff <= 0:
        return amount
    # Use divmod-style truncation toward zero (matches Go's int64 division)
    if amount < 0:
        return -((-amount) // (10 ** diff))
    return amount // (10 ** diff)


def format_amount(amount: int, scale: int) -> str:
    """Format an integer amount at the given scale as a human-readable string.

    Example:
        ``format_amount(1234567, 2)`` returns ``"12345.67"``
        ``format_amount(-500, 2)`` returns ``"-5.00"``
        ``format_amount(100, 0)`` returns ``"100"``

    Args:
        amount: The integer amount in minor units.
        scale: Number of decimal places for the currency.

    Returns:
        Formatted string with appropriate decimal placement.
    """
    if scale == 0:
        return str(amount)

    sign = "-" if amount < 0 else ""
    abs_amount = abs(amount)

    int_part = abs_amount // (10 ** scale)
    frac_part = abs_amount % (10 ** scale)

    return f"{sign}{int_part}.{frac_part:0>{scale}d}"


def parse_amount(s: str, scale: int) -> int:
    """Parse a human-readable decimal string into an integer at the given scale.

    Example:
        ``parse_amount("12345.67", 2)`` returns ``1_234_567``
        ``parse_amount("-5.00", 2)`` returns ``-500``
        ``parse_amount("100", 2)`` returns ``10_000``

    Args:
        s: The decimal string to parse.
        scale: Number of decimal places expected.

    Returns:
        The integer amount in minor units.

    Raises:
        ValueError: If the string is not a valid decimal number or has
            more fractional digits than *scale*.
    """
    if not s:
        raise ValueError("empty string")

    negative = False
    text = s.strip()
    if text.startswith("-"):
        negative = True
        text = text[1:]

    if "." not in text:
        whole = int(text)
        frac = 0
    else:
        parts = text.split(".", 1)
        whole = int(parts[0]) if parts[0] else 0
        frac_str = parts[1]

        if len(frac_str) > scale:
            raise ValueError(
                f"too many fractional digits: got {len(frac_str)}, expected at most {scale}"
            )

        # Pad fractional part to exact scale width
        frac_str = frac_str.ljust(scale, "0")
        frac = int(frac_str)

    result = whole * (10 ** scale) + frac
    return -result if negative else result
