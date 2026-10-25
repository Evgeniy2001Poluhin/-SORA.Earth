"""Validate stand controls before any Docker call; never accepts an endpoint."""
import math
import sys
from urllib.parse import urlencode


BOUNDS = {
    "drift_on": (0, 1), "rejected_on": (0, 1),
    "gap_present": (0, 1), "ts_present": (0, 1),
    "gap": (0, 10000), "lat_n": (0, 10000),
    "lat_ms": (0, 1000000), "ts_lag": (0, 100000000),
    "retrain_since": (-1, 100000000),
}
INTEGER = {"drift_on", "rejected_on", "gap_present", "ts_present", "gap", "lat_n"}


def query(arguments):
    if not arguments or len(arguments) > len(BOUNDS) + 1:
        raise ValueError("expected bounded key=value controls")
    values = {}
    for argument in arguments:
        key, separator, value = argument.partition("=")
        if not separator or key in values or len(value) > 32:
            raise ValueError("invalid or duplicate control")
        if key == "app":
            if value not in {"ok", "down"}:
                raise ValueError("invalid app state")
        elif key in BOUNDS:
            number = float(value)
            low, high = BOUNDS[key]
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError("control outside supported range")
            if key in INTEGER and not number.is_integer():
                raise ValueError("expected an integer control")
        else:
            raise ValueError("unknown control")
        values[key] = value
    return urlencode(values)


if __name__ == "__main__":
    try:
        print(query(sys.argv[1:]))
    except ValueError:
        raise SystemExit("stand refused: invalid control arguments") from None
