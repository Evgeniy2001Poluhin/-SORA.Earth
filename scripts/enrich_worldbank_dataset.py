#!/usr/bin/env python3
"""Enrich dataset with real World Bank projects.

Fetches 22,000+ projects from World Bank API, maps to our schema, and merges
with existing projects.csv. Includes GDP per capita enrichment from WDI API.

Usage:
    python3 scripts/enrich_worldbank_dataset.py --output data/projects_enriched_wb.csv
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests
from tqdm import tqdm


# World Bank API endpoints
WB_PROJECTS_API = "https://search.worldbank.org/api/v2/projects"
WB_GDP_API = "https://api.worldbank.org/v2/country/{country_code}/indicator/NY.GDP.PCAP.CD"

# Sector weights for CO2 reduction estimation (tonnes/year per $1M budget)
SECTOR_CO2_WEIGHTS = {
    "Energy": 2500,           # High impact: renewable energy, efficiency
    "Transport": 2000,        # High impact: sustainable transport
    "Environment": 1500,      # Medium-high: forests, conservation
    "Water": 800,             # Medium: water efficiency, treatment
    "Agriculture": 600,       # Medium-low: sustainable farming
    "Urban Development": 500, # Low-medium: green buildings
    "Industry": 400,          # Low: industrial efficiency
    "default": 300,           # Default estimate
}

# Theme weights for social impact scoring (1-10 scale)
THEME_SOCIAL_WEIGHTS = {
    "Social Protection": 9,
    "Rural Development": 8,
    "Human Development": 8,
    "Gender": 8,
    "Health": 8,
    "Education": 7,
    "Urban Development": 7,
    "Social Inclusion": 9,
    "Jobs": 7,
    "Poverty": 9,
    "default": 5,
}

# Success ratings mapping
SUCCESS_RATINGS = {
    "Highly Satisfactory": 1,
    "Satisfactory": 1,
    "Moderately Satisfactory": 0,
    "Moderately Unsatisfactory": 0,
    "Unsatisfactory": 0,
    "Highly Unsatisfactory": 0,
}


def fetch_wb_projects(rows_per_page: int = 500, max_projects: int = 22000) -> List[Dict]:
    """Fetch World Bank projects with pagination."""
    projects = []
    page = 0

    params = {
        "format": "json",
        "rows": rows_per_page,
        "source": "IBRD,IDA",
        # No sector filter - get all projects for maximum coverage
    }

    print(f"Fetching World Bank projects (target: {max_projects})...")
    pbar = tqdm(total=max_projects, desc="Projects fetched")

    while len(projects) < max_projects:
        params["os"] = page * rows_per_page  # offset

        try:
            response = requests.get(WB_PROJECTS_API, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if "projects" not in data:
                print(f"Warning: No 'projects' key in response at page {page}")
                break

            batch_dict = data["projects"]
            if not batch_dict:
                print(f"No more projects at page {page}")
                break

            # Projects is a dict with project_id as key
            batch = list(batch_dict.values())
            projects.extend(batch)
            pbar.update(len(batch))
            page += 1

            # If we got fewer projects than requested, we've reached the end
            if len(batch) < rows_per_page:
                print(f"Reached end of results at page {page} (got {len(batch)} < {rows_per_page})")
                break

            # Rate limiting
            time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            print(f"Error fetching page {page}: {e}")
            break
        except json.JSONDecodeError as e:
            print(f"JSON decode error at page {page}: {e}")
            break

    pbar.close()
    print(f"Fetched {len(projects)} projects total")
    return projects[:max_projects]


def calculate_duration_months(approval_date: str, closing_date: str) -> Optional[float]:
    """Calculate project duration in months."""
    try:
        if not approval_date or not closing_date:
            return None

        # Parse dates (format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)
        approval = datetime.fromisoformat(approval_date.replace("Z", "+00:00"))
        closing = datetime.fromisoformat(closing_date.replace("Z", "+00:00"))

        delta = closing - approval
        months = delta.days / 30.44  # Average days per month

        return max(1, months)  # At least 1 month
    except (ValueError, AttributeError):
        return None


def estimate_co2_reduction(budget: float, sectors: List[str]) -> float:
    """Estimate CO2 reduction based on budget and sector weights."""
    if not sectors or budget <= 0:
        return 0.0

    # Get highest weight from project sectors
    max_weight = max(
        (SECTOR_CO2_WEIGHTS.get(s.strip(), SECTOR_CO2_WEIGHTS["default"]) for s in sectors),
        default=SECTOR_CO2_WEIGHTS["default"]
    )

    # CO2 = (budget in millions) * sector_weight + random variation
    budget_millions = budget / 1_000_000
    base_co2 = budget_millions * max_weight

    # Add +/- 20% variation
    import random
    variation = random.uniform(0.8, 1.2)

    return max(0, base_co2 * variation)


def estimate_social_impact(themes: List[str]) -> int:
    """Estimate social impact score (1-10) based on themes."""
    if not themes:
        return THEME_SOCIAL_WEIGHTS["default"]

    # Average of theme weights
    weights = [
        THEME_SOCIAL_WEIGHTS.get(t.strip(), THEME_SOCIAL_WEIGHTS["default"])
        for t in themes
    ]

    avg_weight = sum(weights) / len(weights)

    # Add +/- 1 point variation
    import random
    variation = random.uniform(-1, 1)

    return max(1, min(10, int(avg_weight + variation)))


def determine_success(status: str, rating: Optional[str]) -> int:
    """Determine project success (0 or 1)."""
    if status != "Closed":
        return 0  # Only closed projects can be evaluated

    if not rating:
        return 0  # No rating = unsuccessful

    return SUCCESS_RATINGS.get(rating, 0)


def fetch_gdp_per_capita(country_code: str, year: int = 2023) -> Optional[float]:
    """Fetch GDP per capita from World Bank WDI API."""
    try:
        url = WB_GDP_API.format(country_code=country_code)
        params = {"format": "json", "date": f"{year}:{year}"}

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if len(data) > 1 and data[1]:
            indicator_data = data[1][0]
            gdp = indicator_data.get("value")
            if gdp:
                return float(gdp)

        return None
    except Exception:
        return None


def map_wb_project_to_schema(project: Dict, gdp_cache: Dict[str, float]) -> Optional[Dict]:
    """Map World Bank project to our schema."""
    try:
        # Extract fields
        project_id = project.get("id", "")
        project_name = project.get("project_name", "Unknown")

        # Country (may be list or string)
        country_name_raw = project.get("countryname") or project.get("countryshortname", "")
        if isinstance(country_name_raw, list):
            country = country_name_raw[0] if country_name_raw else "Unknown"
        else:
            country = country_name_raw or "Unknown"

        country_code_raw = project.get("countrycode") or project.get("countryshortname", "")
        if isinstance(country_code_raw, list):
            country_code = country_code_raw[0] if country_code_raw else ""
        else:
            country_code = country_code_raw or ""

        # Budget (may be string with commas like "200,000,000")
        total_amt_str = project.get("totalamt", "0")
        if isinstance(total_amt_str, str):
            total_amt = float(total_amt_str.replace(",", ""))
        else:
            total_amt = float(total_amt_str or 0)

        if total_amt <= 0:
            return None  # Skip projects without budget

        # Duration
        approval_date = project.get("boardapprovaldate") or project.get("approvaldate")
        closing_date = project.get("closingdate") or project.get("closing_date")

        # If no dates, estimate from approval year
        if not approval_date or not closing_date:
            approval_fy = project.get("approvalfy")
            if approval_fy:
                # Estimate: projects typically run 5 years
                try:
                    approval_year = int(approval_fy)
                    duration_months = 60.0  # Default 5 years
                except (ValueError, TypeError):
                    duration_months = None
            else:
                duration_months = None
        else:
            duration_months = calculate_duration_months(approval_date, closing_date)

        if not duration_months or duration_months < 1 or duration_months > 240:
            return None  # Skip invalid durations

        # Sectors and themes (may be in different formats)
        sectors_raw = project.get("sector") or project.get("sector1", {})
        if isinstance(sectors_raw, dict):
            sectors_str = sectors_raw.get("Name", "")
        elif isinstance(sectors_raw, str):
            sectors_str = sectors_raw
        else:
            sectors_str = ""
        sectors = [s.strip() for s in sectors_str.split(",") if s.strip()] if sectors_str else []

        themes_raw = project.get("theme") or project.get("theme1", "")
        if isinstance(themes_raw, str):
            themes_str = themes_raw
        else:
            themes_str = str(themes_raw) if themes_raw else ""
        themes = [t.strip() for t in themes_str.split(",") if t.strip() and t.strip() != "!$!0"]

        # Calculate derived fields
        co2_reduction = estimate_co2_reduction(total_amt, sectors)
        social_impact = estimate_social_impact(themes)

        # Success determination
        status = project.get("status", "") or project.get("projectstatusdisplay", "")
        rating = project.get("rating") or project.get("icrrating") or project.get("icrline")
        success = determine_success(status, rating)

        # GDP per capita
        # Extract country code (may be list)
        if isinstance(country_code, list):
            country_code = country_code[0] if country_code else ""

        if country_code and country_code not in gdp_cache:
            gdp = fetch_gdp_per_capita(country_code)
            gdp_cache[country_code] = gdp if gdp else 12720.0  # Default median
            time.sleep(0.1)  # Rate limit

        gdp_per_capita = gdp_cache.get(country_code, 12720.0)

        # Region mapping (simplified)
        region = project.get("regionname", "Other") or "Other"

        # Category (primary sector) - clean up
        if sectors and sectors[0]:
            category = sectors[0].strip()
        else:
            category = "Other"

        return {
            "project_id": project_id,
            "project_name": project_name,
            "budget": total_amt,
            "duration_months": duration_months,
            "co2_reduction": co2_reduction,
            "social_impact": social_impact,
            "success": success,
            "country": country,
            "country_code": country_code,
            "country_gdp_per_capita": gdp_per_capita,
            "region": region,
            "category": category,
            "status": status,
            "rating": rating or "Not Rated",
            "sectors": sectors_str,
            "themes": themes_str,
            "source": "WorldBank",
        }

    except Exception as e:
        print(f"Error mapping project {project.get('id')}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Enrich dataset with World Bank projects")
    parser.add_argument("--output", default="data/projects_enriched_wb.csv", help="Output CSV file")
    parser.add_argument("--max-projects", type=int, default=22000, help="Max projects to fetch")
    parser.add_argument("--existing", default="data/projects.csv", help="Existing projects CSV")
    args = parser.parse_args()

    # Step 1: Fetch World Bank projects
    wb_projects = fetch_wb_projects(rows_per_page=500, max_projects=args.max_projects)

    if not wb_projects:
        print("No projects fetched. Exiting.")
        sys.exit(1)

    # Step 2: Map to our schema
    print(f"\nMapping {len(wb_projects)} projects to schema...")
    gdp_cache = {}
    mapped_projects = []

    for project in tqdm(wb_projects, desc="Mapping projects"):
        mapped = map_wb_project_to_schema(project, gdp_cache)
        if mapped:
            mapped_projects.append(mapped)

    print(f"Successfully mapped {len(mapped_projects)} projects")

    # Step 3: Load existing dataset
    if os.path.exists(args.existing):
        print(f"\nLoading existing dataset from {args.existing}...")
        existing_df = pd.read_csv(args.existing)
        print(f"Loaded {len(existing_df)} existing projects")

        # Add source column if missing
        if "source" not in existing_df.columns:
            existing_df["source"] = "Original"

        # Merge
        wb_df = pd.DataFrame(mapped_projects)
        combined_df = pd.concat([existing_df, wb_df], ignore_index=True)
    else:
        print(f"\nNo existing dataset at {args.existing}, using only World Bank data")
        combined_df = pd.DataFrame(mapped_projects)

    # Step 4: Remove duplicates (if project_id exists)
    print(f"\nTotal projects before deduplication: {len(combined_df)}")
    if "project_id" in combined_df.columns:
        # Remove rows with missing project_id first, then deduplicate
        combined_df = combined_df[combined_df["project_id"].notna() & (combined_df["project_id"] != "")]
        combined_df = combined_df.drop_duplicates(subset=["project_id"], keep="first")
    print(f"Total projects after deduplication: {len(combined_df)}")

    # Step 5: Save
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    combined_df.to_csv(args.output, index=False)
    print(f"\n✅ Saved enriched dataset to {args.output}")

    # Step 6: Statistics
    print("\n" + "="*60)
    print("DATASET STATISTICS")
    print("="*60)
    print(f"Total projects: {len(combined_df)}")
    print(f"  - Original: {len(combined_df[combined_df['source'] != 'WorldBank'])}")
    print(f"  - World Bank: {len(combined_df[combined_df['source'] == 'WorldBank'])}")
    print(f"\nSuccess rate: {combined_df['success'].mean():.2%}")
    print(f"Average budget: ${combined_df['budget'].mean():,.0f}")
    print(f"Average duration: {combined_df['duration_months'].mean():.1f} months")
    print(f"Average CO2 reduction: {combined_df['co2_reduction'].mean():.0f} tonnes/year")
    print(f"Average social impact: {combined_df['social_impact'].mean():.1f}/10")
    print(f"\nTop 5 countries:")
    print(combined_df['country'].value_counts().head())
    print(f"\nTop 5 categories:")
    print(combined_df['category'].value_counts().head())


if __name__ == "__main__":
    main()
