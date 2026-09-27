"""Spreadsheet formula neutralization for CSV exports.

A leading = + - or @ in a CSV cell is treated as a formula by Excel and
LibreOffice Calc, so user-controlled text written to a cell without escaping
can execute arbitrary formulas. Prefixing with a single quote neutralizes it:
the quote is the spreadsheet's own escape marker and renders as nothing.
"""


def neutralize_cell(value: str, keep_numbers: bool = True) -> str:
    """Quote a leading spreadsheet formula trigger.

    A cell starting with = + - or @ is treated as a formula in Excel, Google
    Sheets and LibreOffice Calc. Prefixing with a single quote neutralizes it
    without changing how the value appears when rendered.

    Numbers are left unchanged by default (keep_numbers=True): a number cannot
    carry a formula, and quoting it turns it into text (a negative number like
    -3.5 would become '-3.5). CSV exports need numeric cells to stay numeric.

    The audit log (keep_numbers=False) quotes everything with a leading trigger,
    even numbers, because the log is text: a quoted "+1" is still readable, but
    an unquoted one could become a formula if the log is later opened in Excel.

    The neutralization does not apply in reverse: a cell that already starts
    with a quote is left alone, because two quotes would show one literally.
    """
    if not value:
        return value

    # Numbers are safe in CSV columns (they must stay numeric), but in plain
    # text like the audit log, we quote everything with a leading trigger.
    if keep_numbers:
        try:
            num = float(value)
            if not (num != num or num == float('inf') or num == float('-inf')):  # not NaN or ±inf
                return value
        except (ValueError, OverflowError):
            pass

    if value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value
