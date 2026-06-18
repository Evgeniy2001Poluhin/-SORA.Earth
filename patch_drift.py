import ast, sys
f = "app/drift_detection.py"
s = open(f, encoding="utf-8").read()

if "import redis" not in s:
    s = s.replace("import logging\n", "import logging\nimport os\nimport json\nimport redis\n", 1)

repl = [
(
"        self._observations = []\n        self._baseline = {}\n",
"        self._observations = []\n        self._baseline = {}\n        self._r = redis.from_url(os.environ.get(\"REDIS_URL\", \"redis://redis:6379/0\"), decode_responses=True)\n        self._k_base = \"drift:baseline\"\n        self._k_obs = \"drift:observations\"\n"
),
(
"    def set_baseline(self, baseline: dict):\n        self._baseline = dict(baseline or {})",
"    def set_baseline(self, baseline: dict):\n        self._r.set(self._k_base, json.dumps(dict(baseline or {})))"
),
(
"    def get_baseline(self):\n        return dict(self._baseline)",
"    def get_baseline(self):\n        raw = self._r.get(self._k_base)\n        return json.loads(raw) if raw else {}"
),
(
"    def add_observation(self, features: dict):\n        self._observations.append(features)\n        if len(self._observations) > self.window_size:\n            self._observations = self._observations[-self.window_size:]",
"    def add_observation(self, features: dict):\n        self._r.rpush(self._k_obs, json.dumps(features))\n        self._r.ltrim(self._k_obs, -self.window_size, -1)"
),
(
"    @property\n    def recent_data(self):\n        return self._observations",
"    @property\n    def recent_data(self):\n        return self.get_observations()"
),
(
"    def get_observations(self):\n        return self._observations",
"    def get_observations(self):\n        return [json.loads(x) for x in self._r.lrange(self._k_obs, 0, -1)]"
),
(
"    def count(self):\n        return len(self._observations)",
"    def count(self):\n        return self._r.llen(self._k_obs)"
),
(
"    def _baseline_drift_check(self):\n        total = len(self._observations)",
"    def _baseline_drift_check(self):\n        _obs = self.get_observations()\n        _base = self.get_baseline()\n        total = len(_obs)"
),
]

for old, new in repl:
    if old not in s:
        print("ANCHOR NOT FOUND:\n" + old[:60])
        sys.exit(1)
    s = s.replace(old, new, 1)

s = s.replace("if not self._baseline:", "if not _base:")
s = s.replace("current_df = pd.DataFrame(self._observations)", "current_df = pd.DataFrame(_obs)")
s = s.replace("if mean_key not in self._baseline:", "if mean_key not in _base:")
s = s.replace("baseline_mean = float(self._baseline[mean_key])", "baseline_mean = float(_base[mean_key])")
s = s.replace("baseline_std = float(self._baseline.get(std_key, 0.0) or 0.0)", "baseline_std = float(_base.get(std_key, 0.0) or 0.0)")
s = s.replace('"baseline_features": sorted(self._baseline.keys()),', '"baseline_features": sorted(_base.keys()),')

ast.parse(s)
open(f, "w", encoding="utf-8").write(s)
print("PATCH OK")
