import time
from collections import defaultdict


class Metrics:
    def __init__(self):
        self.counters = defaultdict(int)
        self.histograms = defaultdict(list)
        self.start_time = time.time()

    def inc(self, name: str, value: int = 1):
        self.counters[name] += value

    def observe(self, name: str, value: float):
        self.histograms[name].append(value)

    def summary(self):
        result = {"uptime_seconds": round(time.time() - self.start_time, 2), "counters": dict(self.counters)}
        for name, values in self.histograms.items():
            if values:
                result[f"{name}_count"] = len(values)
                result[f"{name}_avg_ms"] = round(sum(values) / len(values), 2)
                result[f"{name}_max_ms"] = round(max(values), 2)
        return result

    def prometheus_format(self):
        lines = []
        lines.append(f"# HELP uptime_seconds Server uptime")
        lines.append(f"uptime_seconds {round(time.time() - self.start_time, 2)}")
        for k, v in self.counters.items():
            lines.append(f"# HELP {k} Counter")
            lines.append(f"{k} {v}")
        for name, values in self.histograms.items():
            if values:
                lines.append(f"{name}_count {len(values)}")
                lines.append(f"{name}_sum {round(sum(values), 2)}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
