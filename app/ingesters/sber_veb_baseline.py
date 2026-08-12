from __future__ import annotations
from datetime import datetime, timezone
from app.ingesters.base import BaseIngester, Signal
from app.ingesters import temporal

BASELINE = {"RU-MOW": 89.0, "RU-MOS": 79.7, "RU-BEL": 79.5, "RU-BRY": 88.7, "RU-VLA": 84.5, "RU-VOR": 85.8, "RU-IVA": 82.9, "RU-KLU": 79.7, "RU-KOS": 79.8, "RU-KRS": 80.3, "RU-LIP": 82.0, "RU-ORL": 82.7, "RU-RYA": 86.2, "RU-SMO": 88.2, "RU-TAM": 83.6, "RU-TVE": 79.9, "RU-TUL": 82.4, "RU-YAR": 80.4, "RU-SPE": 77.0, "RU-LEN": 82.7, "RU-KR": 78.5, "RU-KO": 75.0, "RU-ARK": 76.6, "RU-NEN": 75.5, "RU-VLG": 77.5, "RU-KGD": 81.7, "RU-MUR": 78.6, "RU-NGR": 81.1, "RU-PSK": 81.7, "RU-AD": 76.0, "RU-KL": 71.9, "RU-KDA": 71.0, "RU-AST": 79.5, "RU-VGG": 76.9, "RU-ROS": 74.1, "RU-CR": 79.5, "RU-SEV": 80.0, "RU-DA": 67.5, "RU-IN": 72.7, "RU-KB": 76.6, "RU-KC": 66.1, "RU-SE": 68.1, "RU-CE": 73.5, "RU-STA": 74.9, "RU-BA": 81.9, "RU-ME": 79.2, "RU-MO": 72.2, "RU-TA": 75.7, "RU-UD": 77.7, "RU-CU": 48.0, "RU-KIR": 73.7, "RU-NIZ": 78.6, "RU-ORE": 80.6, "RU-PNZ": 75.7, "RU-PER": 77.2, "RU-SAM": 80.2, "RU-SAR": 73.7, "RU-ULY": 74.2, "RU-KGN": 72.8, "RU-SVE": 78.4, "RU-TYU": 78.1, "RU-KHM": 71.4, "RU-YAN": 70.6, "RU-CHE": 73.5, "RU-AL": 69.0, "RU-TY": 45.0, "RU-KK": 71.7, "RU-ALT": 69.3, "RU-KYA": 69.5, "RU-IRK": 72.2, "RU-KEM": 73.4, "RU-NVS": 66.5, "RU-OMS": 73.4, "RU-TOM": 69.3, "RU-BU": 65.5, "RU-SA": 58.0, "RU-ZAB": 70.2, "RU-KAM": 69.9, "RU-PRI": 73.0, "RU-KHA": 66.6, "RU-AMU": 68.6, "RU-MAG": 60.3, "RU-SAK": 62.4, "RU-YEV": 64.0, "RU-CHU": 58.6}

class SberVebBaselineIngester(BaseIngester):
    name = "sber_veb_baseline"
    default_ttl_hours = 8760

    async def fetch(self):
        """A dict of constants. There is no observation time, so none is given.

        This used to stamp `datetime.now()` on every value, and persistence
        recorded that as when the number was observed (#121). Eighty-five
        literals were therefore filed as measured today, every day, and -- since
        the stamp was part of the deduplication key -- inserted again on every
        run.

        The revision is a hash of the whole snapshot, not a hand-written
        version. Someone editing one constant and forgetting to bump a version
        would otherwise have the new value collapse onto the old identity and
        vanish with no error. Hashing the whole dict also means a single changed
        value makes every row a member of a new revision, which is what a
        revision of a snapshot means.
        """
        revision = temporal.content_revision(self.name, BASELINE)
        return [
            Signal(code, self.name, "esg_index_baseline", float(v), "0-100",
                   observed_at=None,
                   temporal_kind=temporal.NOT_APPLICABLE,
                   source_revision=revision)
            for code, v in BASELINE.items()
        ]
