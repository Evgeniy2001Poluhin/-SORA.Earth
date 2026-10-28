"""One pyplot figure at a time in a process.

pyplot keeps a single "current figure" per process, and gunicorn's workers run
sync handlers on a thread pool. shap.plots.* draw onto that current figure, so
two requests rendering at once can draw onto, save or close each other's chart:
an anonymous waterfall was measured saving a signed-in caller's budget into the
shared PNG cache. Every pyplot render therefore holds this lock from the first
draw to the last savefig, and starts and ends with no figure open.

Charts that do not need pyplot build a matplotlib.figure.Figure directly; such a
figure is never registered with pyplot and needs no lock.
"""
import threading
from contextlib import contextmanager

PYPLOT_LOCK = threading.Lock()


@contextmanager
def exclusive_pyplot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with PYPLOT_LOCK:
        plt.close("all")
        try:
            yield plt
        finally:
            plt.close("all")
