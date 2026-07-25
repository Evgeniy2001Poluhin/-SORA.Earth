"""OpenAQ air quality data ingester with data quality pipeline.

Fetches real-time air quality measurements from OpenAQ API v3.
Implements comprehensive data quality checks and validation.

API: https://docs.openaq.org/
Docs: https://docs.openaq.org/using-the-api/quick-start
"""
from __future__ import annotations
import logging, os
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from enum import Enum
from app.ingesters.base import BaseIngester, Signal

log = logging.getLogger(__name__)


class DataQuality(str, Enum):
    """Data quality flags based on validation checks."""
    EXCELLENT = "excellent"  # All checks passed
    GOOD = "good"           # Minor issues (e.g., slightly old data)
    FAIR = "fair"           # Moderate issues (e.g., data age > 3 hours)
    POOR = "poor"           # Significant issues (e.g., out of range)
    INVALID = "invalid"     # Failed validation


# OpenAQ parameter codes and validation ranges
# Source: WHO Air Quality Guidelines & EPA standards
PARAMETER_RANGES = {
    "pm25": {"min": 0, "max": 500, "unit": "µg/m³", "metric": "pm25_ugm3"},
    "pm10": {"min": 0, "max": 600, "unit": "µg/m³", "metric": "pm10_ugm3"},
    "o3": {"min": 0, "max": 500, "unit": "µg/m³", "metric": "o3_ugm3"},
    "no2": {"min": 0, "max": 2000, "unit": "µg/m³", "metric": "no2_ugm3"},
    "so2": {"min": 0, "max": 1000, "unit": "µg/m³", "metric": "so2_ugm3"},
    "co": {"min": 0, "max": 50000, "unit": "µg/m³", "metric": "co_ugm3"},
}

REGION_CAPITALS = [
    # Europe
    ("DEU", 52.5200, 13.4050),   # Berlin
    ("GBR", 51.5074, -0.1278),   # London
    ("FRA", 48.8566, 2.3522),    # Paris
    ("ESP", 40.4168, -3.7038),   # Madrid
    ("ITA", 41.9028, 12.4964),   # Rome
    ("NLD", 52.3676, 4.9041),    # Amsterdam
    ("POL", 52.2297, 21.0122),   # Warsaw
    # Asia
    ("IND", 28.6139, 77.2090),   # New Delhi
    ("CHN", 39.9042, 116.4074),  # Beijing
    ("JPN", 35.6762, 139.6503),  # Tokyo
    ("KOR", 37.5665, 126.9780),  # Seoul
    ("IDN", -6.2088, 106.8456),  # Jakarta
    # Americas
    ("USA", 38.9072, -77.0369),  # Washington DC
    ("BRA", -15.7975, -47.8919), # Brasilia
    ("MEX", 19.4326, -99.1332),  # Mexico City
    ("CAN", 45.4215, -75.6972),  # Ottawa
    # Africa
    ("NGA", 9.0579, 7.4951),     # Abuja
    ("ZAF", -25.7479, 28.2293),  # Pretoria
    ("KEN", -1.2921, 36.8219),   # Nairobi
    # Russia
    ("RU-MOW", 55.7558, 37.6173),
    ("RU-SPE", 59.9343, 30.3351),
]

PARAMETERS = ("pm25", "pm10", "no2", "o3", "so2", "co")


class OpenAQIngester(BaseIngester):
    """Ingester for OpenAQ air quality data with data quality pipeline.

    Features:
    - Real-time air quality measurements (PM2.5, PM10, O3, NO2, SO2, CO)
    - Comprehensive data quality checks (range, freshness, completeness)
    - Outlier detection using IQR method
    - Data quality scoring and flagging

    Rate limits: 2000 requests/hour (v3 API, with API key)
    """
    name = "openaq"
    default_ttl_hours = 1  # Air quality data changes frequently
    max_retries = 3
    timeout_s = 30.0

    def __init__(self):
        super().__init__()
        self.quality_stats = {
            "total": 0,
            "excellent": 0,
            "good": 0,
            "fair": 0,
            "poor": 0,
            "invalid": 0,
        }

    async def fetch(self) -> List[Signal]:
        """Fetch air quality data with quality validation.

        Returns:
            List of Signal objects with air quality measurements and quality flags
        """
        key = os.environ.get("OPENAQ_API_KEY")
        if not key:
            log.info("[openaq] no OPENAQ_API_KEY set, skipping")
            return []

        all_signals = []
        now = datetime.now(timezone.utc)

        async with self.client() as c:
            c.headers["X-API-Key"] = key

            for code, lat, lon in REGION_CAPITALS:
                try:
                    # Find nearby locations
                    r = await c.get(
                        "https://api.openaq.org/v3/locations",
                        params={"coordinates": f"{lat},{lon}", "radius": 25000, "limit": 5},
                    )
                    r.raise_for_status()
                    locs = r.json().get("results", [])
                    if not locs:
                        log.debug(f"[openaq] {code} - no locations found")
                        continue

                    # Fetch latest measurements from the best location
                    loc_id = locs[0].get("id")
                    lr = await c.get(f"https://api.openaq.org/v3/locations/{loc_id}/latest")
                    lr.raise_for_status()

                    # Process each measurement with quality checks
                    for measurement in lr.json().get("results", []):
                        signal = self._process_measurement(measurement, code, now)
                        if signal:
                            all_signals.append(signal)

                except Exception as e:
                    log.warning(f"[openaq] {code} failed: {e}")

        # Apply cross-location quality checks
        all_signals = self._detect_outliers(all_signals)

        # Log quality statistics
        self._log_quality_stats()

        log.info(
            f"[openaq] fetched {len(all_signals)} signals from {len(REGION_CAPITALS)} regions "
            f"(quality: {self.quality_stats['excellent']} excellent, "
            f"{self.quality_stats['good']} good, "
            f"{self.quality_stats['invalid']} invalid)"
        )
        return all_signals

    def _process_measurement(
        self,
        measurement: Dict[str, Any],
        region_code: str,
        now: datetime
    ) -> Optional[Signal]:
        """Process and validate a single measurement.

        Args:
            measurement: Measurement data from OpenAQ API
            region_code: Region code (e.g., "DEU", "USA")
            now: Current timestamp

        Returns:
            Signal object if valid, None if invalid
        """
        # Extract parameter info
        param_obj = measurement.get("parameter") or {}
        param = param_obj.get("name", "")
        if param not in PARAMETERS:
            return None

        value = measurement.get("value")
        if value is None:
            return None

        # Extract observation timestamp
        period = measurement.get("period", {})
        datetime_last = period.get("datetimeLast", {})
        obs_time_str = datetime_last.get("utc")

        if obs_time_str:
            try:
                obs_time = datetime.fromisoformat(obs_time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                obs_time = now
        else:
            obs_time = now

        # Run data quality checks
        quality, issues = self._validate_measurement(
            param,
            value,
            obs_time,
            now,
            measurement
        )

        # Update quality statistics
        self.quality_stats["total"] += 1
        self.quality_stats[quality.value] += 1

        # Skip invalid measurements
        if quality == DataQuality.INVALID:
            log.debug(f"[openaq] {region_code} {param} invalid: {issues}")
            return None

        # Get parameter metadata
        param_meta = PARAMETER_RANGES.get(param, {"metric": f"{param}_ugm3", "unit": "µg/m³"})

        # Build signal with quality metadata
        return Signal(
            region_code=region_code,
            source=self.name,
            metric=param_meta["metric"],
            value=float(value),
            unit="ug/m3",
            observed_at=obs_time,
            metadata={
                "quality": quality.value,
                "quality_issues": issues,
                "data_age_hours": (now - obs_time).total_seconds() / 3600,
                "unit_original": measurement.get("unit"),
                "location_id": measurement.get("locationId"),
            }
        )

    def _validate_measurement(
        self,
        param: str,
        value: float,
        obs_time: datetime,
        now: datetime,
        raw_data: Dict[str, Any]
    ) -> tuple[DataQuality, List[str]]:
        """Run comprehensive data quality checks.

        Validation checks:
        1. Range check: Value within expected physical bounds
        2. Freshness check: Data not too old
        3. Completeness check: Required fields present
        4. Unit consistency: Unit matches expected
        5. Suspicious values: Check for anomalous readings

        Args:
            param: Parameter code (e.g., "pm25")
            value: Measured value
            obs_time: Observation timestamp
            now: Current timestamp
            raw_data: Raw API response data

        Returns:
            Tuple of (quality_flag, list_of_issues)
        """
        issues = []
        param_meta = PARAMETER_RANGES.get(param)
        if not param_meta:
            return DataQuality.INVALID, ["unknown_parameter"]

        # 1. Range validation
        if value < param_meta["min"] or value > param_meta["max"]:
            issues.append(f"out_of_range[{param_meta['min']}-{param_meta['max']}]")

        # 2. Freshness check
        data_age = now - obs_time
        if data_age > timedelta(hours=24):
            issues.append(f"stale_data[age={data_age.total_seconds()/3600:.1f}h]")
        elif data_age > timedelta(hours=3):
            issues.append(f"old_data[age={data_age.total_seconds()/3600:.1f}h]")

        # 3. Unit consistency check
        expected_unit = param_meta["unit"]
        actual_unit = raw_data.get("unit")
        if actual_unit and actual_unit not in [expected_unit, "µg/m³", "ug/m3", "μg/m³"]:
            issues.append(f"unit_mismatch[expected={expected_unit},got={actual_unit}]")

        # 4. Completeness check
        if not raw_data.get("locationId"):
            issues.append("missing_location_id")

        # 5. Suspicious zero check (PM2.5/PM10 exactly 0 is rare)
        if param in ["pm25", "pm10"] and value == 0:
            issues.append("suspicious_zero")

        # 6. Negative value check (should never happen after range check, but double-check)
        if value < 0:
            issues.append("negative_value")

        # Determine overall quality
        if any(issue == "negative_value" or issue.startswith("out_of_range[") for issue in issues):
            return DataQuality.INVALID, issues

        if len(issues) == 0:
            return DataQuality.EXCELLENT, issues

        if any("stale_data" in issue for issue in issues):
            return DataQuality.POOR, issues

        if any("old_data" in issue for issue in issues):
            return DataQuality.FAIR, issues

        return DataQuality.GOOD, issues

    def _detect_outliers(self, signals: List[Signal]) -> List[Signal]:
        """Detect statistical outliers using IQR method.

        Applies IQR (Interquartile Range) outlier detection per metric.
        Flags values outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR] as potential outliers.

        Args:
            signals: List of signals to check

        Returns:
            List of signals with outlier flags added to metadata
        """
        if len(signals) < 4:  # Need at least 4 points for IQR
            return signals

        # Group signals by metric
        by_metric: Dict[str, List[Signal]] = {}
        for signal in signals:
            if signal.metric not in by_metric:
                by_metric[signal.metric] = []
            by_metric[signal.metric].append(signal)

        # Detect outliers for each metric
        for metric, metric_signals in by_metric.items():
            if len(metric_signals) < 4:
                continue

            values = [s.value for s in metric_signals if s.value is not None]
            if not values:
                continue

            values.sort()

            # Calculate IQR
            n = len(values)
            q1 = values[n // 4]
            q3 = values[3 * n // 4]
            iqr = q3 - q1

            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr

            # Flag outliers
            for signal in metric_signals:
                if signal.value is None:
                    continue

                if signal.value < lower_bound or signal.value > upper_bound:
                    if not signal.metadata:
                        signal.metadata = {}
                    signal.metadata["outlier"] = True
                    signal.metadata["iqr_bounds"] = f"[{lower_bound:.1f}, {upper_bound:.1f}]"

                    # Downgrade quality for outliers
                    current_quality = signal.metadata.get("quality", "good")
                    if current_quality == "excellent":
                        signal.metadata["quality"] = "good"
                        issues = signal.metadata.setdefault("quality_issues", [])
                        if isinstance(issues, list):
                            issues.append("statistical_outlier")

        return signals

    def _log_quality_stats(self):
        """Log quality statistics summary."""
        total = self.quality_stats["total"]
        if total == 0:
            return

        excellent_pct = self.quality_stats["excellent"] / total * 100
        good_pct = self.quality_stats["good"] / total * 100
        invalid_pct = self.quality_stats["invalid"] / total * 100

        log.info(
            f"[openaq] quality summary: "
            f"{excellent_pct:.1f}% excellent, "
            f"{good_pct:.1f}% good, "
            f"{invalid_pct:.1f}% invalid "
            f"(n={total})"
        )
