"""Where a project's marker sits on the map, relative to its country's centre.

Markers for projects in one country would stack on the same point, so each is
nudged by an offset derived from the project's name. The offset has to be a
property of the name: the same project, evaluated twice, belongs in one place.

It used `hash(name)`, which Python randomises per process. With four gunicorn
workers and no `PYTHONHASHSEED`, one project's marker moved by up to 2.7
degrees depending on the worker that answered, and again after every restart.
SHA-256 of the name is the same in every process; the range is unchanged.
"""
import hashlib


def marker_offset(name: str) -> float:
    """Degrees to shift both latitude and longitude, in [-1.5, 1.2]."""
    digest = hashlib.sha256((name or "").encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") % 10 - 5) * 0.3
