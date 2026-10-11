"""Country ESG benchmarks.

CO2 per capita, GDP per capita, and HDI refreshed to one common year for all countries
from named World Bank and UNDP series (see SOURCES). Other fields carried over unchanged.
"""

# Data provenance
RETRIEVED = "2026-09-29"

SOURCES = {
    "co2_per_capita": {
        "source": "World Bank",
        "series": "EN.GHG.CO2.PC.CE.AR5",
        "year": 2024,
        "note": "Carbon dioxide (CO2) emissions excluding LULUCF, tonnes CO2e per capita",
    },
    "gdp_per_capita": {
        "source": "World Bank",
        "series": "NY.GDP.PCAP.CD",
        "year": 2024,
        "note": "GDP per capita, current US$",
    },
    "hdi": {
        "source": "UNDP Human Development Report",
        "series": "HDI",
        "year": 2022,
        "note": "Human Development Index; 2022 is the latest year present for all 31 countries in the UNDP HDR time series downloaded on 2026-09-29",
    },
    "renewable_share": {
        "source": "World Bank",
        "series": "EG.ELC.RNEW.ZS",
        "year": None,
        "years": "2011-2021",
        "note": "Renewable share of electricity output; values carried over unchanged from prior dataset. They match this series for years that vary by country (2011-2021, several only approximately), and the series has no year common to all 31 countries after 2011, so it was not refreshed. Finland's value is the 2021 value of this series (added 2026-09-30).",
    },
    "esg_rank": {
        "source": None,
        "series": None,
        "year": None,
        "note": "Source and year not established; not used by the ESG score (see calculate_esg in app/main.py). Finland has no esg_rank (null).",
    },
    "gini_index": {
        "source": None,
        "series": None,
        "year": None,
        "note": "Source and year not established for 30 countries; not used by the ESG score (see calculate_esg in app/main.py). Finland's value is World Bank SI.POV.GINI 2023.",
    },
    "gov_effectiveness": {
        "source": None,
        "series": None,
        "year": None,
        "note": "Source and year not established; not used by the ESG score (see calculate_esg in app/main.py). Finland has no gov_effectiveness (null), as the World Bank WGI series is archived in the API.",
    },
}

BENCHMARKS = {
    "Germany":      {"co2_per_capita": 6.9,  "renewable_share": 46.3, "esg_rank": 8,  "hdi": 0.95, "gdp_per_capita": 56104,  "gini_index": 31.7, "gov_effectiveness": 1.54},
    "France":       {"co2_per_capita": 4.0,  "renewable_share": 44.2, "esg_rank": 12, "hdi": 0.91, "gdp_per_capita": 46103,  "gini_index": 31.6, "gov_effectiveness": 1.36},
    "USA":          {"co2_per_capita": 13.6, "renewable_share": 21.5, "esg_rank": 35, "hdi": 0.927, "gdp_per_capita": 86170,  "gini_index": 39.8, "gov_effectiveness": 1.47},
    "China":        {"co2_per_capita": 9.3,  "renewable_share": 29.4, "esg_rank": 47, "hdi": 0.788, "gdp_per_capita": 13293,  "gini_index": 38.2, "gov_effectiveness": 0.51},
    "India":        {"co2_per_capita": 2.2,  "renewable_share": 38.7, "esg_rank": 52, "hdi": 0.644, "gdp_per_capita": 2592,   "gini_index": 35.7, "gov_effectiveness": -0.07},
    "Brazil":       {"co2_per_capita": 2.3,  "renewable_share": 83.2, "esg_rank": 42, "hdi": 0.76, "gdp_per_capita": 10311,   "gini_index": 48.9, "gov_effectiveness": -0.19},
    "Japan":        {"co2_per_capita": 7.8,  "renewable_share": 20.3, "esg_rank": 18, "hdi": 0.92, "gdp_per_capita": 33797,  "gini_index": 32.9, "gov_effectiveness": 1.58},
    "UK":           {"co2_per_capita": 4.2,  "renewable_share": 43.1, "esg_rank": 10, "hdi": 0.94, "gdp_per_capita": 53341,  "gini_index": 35.1, "gov_effectiveness": 1.39},
    "Canada":       {"co2_per_capita": 14.0, "renewable_share": 67.8, "esg_rank": 15, "hdi": 0.935, "gdp_per_capita": 55016,  "gini_index": 33.3, "gov_effectiveness": 1.63},
    "Australia":    {"co2_per_capita": 14.1, "renewable_share": 32.5, "esg_rank": 25, "hdi": 0.946, "gdp_per_capita": 64610,  "gini_index": 34.3, "gov_effectiveness": 1.57},
    "South Korea":  {"co2_per_capita": 11.4, "renewable_share": 8.1,  "esg_rank": 28, "hdi": 0.929, "gdp_per_capita": 36239,  "gini_index": 31.4, "gov_effectiveness": 1.24},
    "Russia":       {"co2_per_capita": 14.0, "renewable_share": 19.7, "esg_rank": 58, "hdi": 0.821, "gdp_per_capita": 14962,  "gini_index": 36.0, "gov_effectiveness": -0.25},
    "Mexico":       {"co2_per_capita": 3.6,  "renewable_share": 26.1, "esg_rank": 55, "hdi": 0.781, "gdp_per_capita": 13988,  "gini_index": 45.4, "gov_effectiveness": -0.13},
    "Italy":        {"co2_per_capita": 5.1,  "renewable_share": 41.0, "esg_rank": 14, "hdi": 0.906, "gdp_per_capita": 40430,  "gini_index": 35.2, "gov_effectiveness": 0.45},
    "Spain":        {"co2_per_capita": 4.5,  "renewable_share": 47.3, "esg_rank": 16, "hdi": 0.911, "gdp_per_capita": 35327,  "gini_index": 34.7, "gov_effectiveness": 0.97},
    "Sweden":       {"co2_per_capita": 3.6,  "renewable_share": 60.1, "esg_rank": 1,  "hdi": 0.952, "gdp_per_capita": 57223,  "gini_index": 30.0, "gov_effectiveness": 1.80},
    "Norway":       {"co2_per_capita": 7.2,  "renewable_share": 71.6, "esg_rank": 3,  "hdi": 0.966, "gdp_per_capita": 89889,  "gini_index": 27.6, "gov_effectiveness": 1.86},
    "Netherlands":  {"co2_per_capita": 6.6,  "renewable_share": 33.2, "esg_rank": 7,  "hdi": 0.946, "gdp_per_capita": 67465,  "gini_index": 29.2, "gov_effectiveness": 1.77},
    "Switzerland":  {"co2_per_capita": 3.7,  "renewable_share": 74.9, "esg_rank": 5,  "hdi": 0.967, "gdp_per_capita": 107702,  "gini_index": 33.1, "gov_effectiveness": 2.02},
    "South Africa": {"co2_per_capita": 6.9,  "renewable_share": 11.3, "esg_rank": 60, "hdi": 0.717, "gdp_per_capita": 6267,   "gini_index": 63.0, "gov_effectiveness": 0.28},
    "Denmark":      {"co2_per_capita": 4.3,  "renewable_share": 62.5, "esg_rank": 2,  "hdi": 0.952, "gdp_per_capita": 71026,  "gini_index": 28.2, "gov_effectiveness": 1.90},
    "Argentina":    {"co2_per_capita": 4.0,  "renewable_share": 31.2, "esg_rank": 48, "hdi": 0.849, "gdp_per_capita": 13970,  "gini_index": 42.3, "gov_effectiveness": -0.17},
    "Indonesia":    {"co2_per_capita": 2.9,  "renewable_share": 34.5, "esg_rank": 53, "hdi": 0.713, "gdp_per_capita": 4925,   "gini_index": 37.9, "gov_effectiveness": 0.04},
    "Saudi Arabia": {"co2_per_capita": 18.5, "renewable_share": 0.4,  "esg_rank": 62, "hdi": 0.875, "gdp_per_capita": 35528,  "gini_index": 45.9, "gov_effectiveness": 0.33},
    "Turkey":       {"co2_per_capita": 5.4,  "renewable_share": 43.8, "esg_rank": 50, "hdi": 0.855, "gdp_per_capita": 15893,  "gini_index": 41.9, "gov_effectiveness": 0.01},
    "Nigeria":      {"co2_per_capita": 0.6,  "renewable_share": 85.3, "esg_rank": 65, "hdi": 0.548, "gdp_per_capita": 1084,   "gini_index": 35.1, "gov_effectiveness": -1.04},
    "Austria":      {"co2_per_capita": 6.3,  "renewable_share": 72.6, "esg_rank": 6,  "hdi": 0.926, "gdp_per_capita": 58269,  "gini_index": 29.7, "gov_effectiveness": 1.52},
    "Albania":      {"co2_per_capita": 1.8,  "renewable_share": 74.3, "esg_rank": 56, "hdi": 0.789, "gdp_per_capita": 11374,   "gini_index": 33.2, "gov_effectiveness": -0.11},
    "Algeria":      {"co2_per_capita": 4.0,  "renewable_share": 1.1,  "esg_rank": 63, "hdi": 0.745, "gdp_per_capita": 5753,   "gini_index": 27.6, "gov_effectiveness": -0.62},
    "Afghanistan":  {"co2_per_capita": 0.3,  "renewable_share": 78.2, "esg_rank": 70, "hdi": 0.462, "gdp_per_capita": 417,    "gini_index": 29.4, "gov_effectiveness": -1.65},
    "Finland":      {"co2_per_capita": 5.5,  "renewable_share": 52.9, "esg_rank": None, "hdi": 0.942, "gdp_per_capita": 53156, "gini_index": 27.4, "gov_effectiveness": None},
}

# `COUNTRIES` in app/main.py -- the list the interface offers and `/evaluate`
# accepts -- spells two of these in full, and `calculate_esg` looks a country up
# by exact name. So "United States" and "United Kingdom" matched nothing and were
# scored on GLOBAL_AVG, while the response went on naming the country the caller
# asked for. Measured on one project (budget 150 000, CO2 340 t/yr, social 9,
# 18 months): the United States scored 68.41 against the world average where its
# own figures give 59.36, and the United Kingdom 71.71 where its own give 78.59.
#
# Aliases rather than renames: "USA" and "UK" are the spellings this table has
# always used and an API caller may send either. They are the same object, so the
# two spellings cannot drift apart. Finland is a different case -- there is no
# Finland row under any spelling -- and is recorded as a gap in
# tests/test_every_selectable_country_has_a_benchmark.py rather than invented.
BENCHMARKS["United States"] = BENCHMARKS["USA"]
BENCHMARKS["United Kingdom"] = BENCHMARKS["UK"]


GLOBAL_AVG = {
    "co2_per_capita": 4.7,
    "renewable_share": 28.3,
    "esg_rank": 50,
    "hdi": 0.739,
    "gdp_per_capita": 13717,
    "gini_index": 35.0,
    "gov_effectiveness": 0.0,
}
