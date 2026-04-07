"""Seed evaluations table with 50 diverse ESG projects."""
import random
from datetime import datetime, timedelta
from app.database import SessionLocal, Evaluation

REGIONS = ["Germany", "Netherlands", "Sweden", "France", "Spain", "Italy",
           "Poland", "Norway", "Finland", "Denmark", "Brazil", "India",
           "China", "USA", "Japan", "Kenya", "South Africa", "Australia"]

COORDS = {
    "Germany": (51.2, 10.4), "Netherlands": (52.1, 5.3), "Sweden": (59.3, 18.1),
    "France": (46.6, 2.2), "Spain": (40.4, -3.7), "Italy": (41.9, 12.5),
    "Poland": (52.2, 21.0), "Norway": (59.9, 10.8), "Finland": (60.2, 24.9),
    "Denmark": (55.7, 12.6), "Brazil": (-15.8, -47.9), "India": (28.6, 77.2),
    "China": (39.9, 116.4), "USA": (38.9, -77.0), "Japan": (35.7, 139.7),
    "Kenya": (-1.3, 36.8), "South Africa": (-33.9, 18.4), "Australia": (-33.9, 151.2),
}

NAMES = [
    "Solar Farm Alpha", "Wind Park Baltic", "Green Hydrogen Hub",
    "Urban Forest Initiative", "EV Charging Network", "Waste-to-Energy Plant",
    "Ocean Cleanup Drones", "Smart Grid Retrofit", "Biogas Collective",
    "Mangrove Restoration", "Carbon Capture Pilot", "Geothermal Station",
    "Agrivoltaics Demo", "Green Roof Program", "Tidal Energy Proto",
    "District Heating Net", "E-Bus Fleet Upgrade", "Circular Plastics Lab",
    "Peatland Rewetting", "Community Solar Co-op", "Offshore Wind Phase2",
    "Hydrogen Ferry Pilot", "Smart Water Metering", "LED Street Retrofit",
    "Bamboo Construction Hub", "Micro-Hydro Village", "Solar Desalination",
    "Vertical Farm Tower", "Heat Pump Subsidy", "Bike Highway Network",
    "Algae Biofuel R&D", "Eco-Cement Factory", "Floating Solar Array",
    "Reforestation Corridor", "Green Bond Fund", "EV Battery Recycling",
    "Passive House Quarter", "Wind-Solar Hybrid", "Methane Capture Mine",
    "Blue Carbon Credits", "Rail Electrification", "Green Data Center",
    "Agro-Forestry Belt", "Plastic Road Pilot", "Solar Canopy Parking",
    "Wetland Filter System", "Compressed Air Storage", "Wave Energy Buoy",
    "Sustainable Textiles", "Green Ammonia Port",
]

def seed():
    db = SessionLocal()
    existing = db.query(Evaluation).count()
    if existing >= 50:
        print(f"Already {existing} evaluations, skipping seed.")
        db.close()
        return

    for i, name in enumerate(NAMES):
        region = REGIONS[i % len(REGIONS)]
        lat, lon = COORDS[region]
        lat += random.uniform(-2, 2)
        lon += random.uniform(-2, 2)
        budget = random.uniform(50_000, 5_000_000)
        co2 = random.uniform(10, 500)
        social = random.uniform(1, 10)
        duration = random.randint(6, 60)
        total = random.uniform(30, 95)
        env = random.uniform(20, 100)
        soc = random.uniform(20, 100)
        eco = random.uniform(20, 100)
        prob = random.uniform(0.3, 0.95)
        risk = random.choice(["low", "medium", "high"])
        rec = f"Project shows {'strong' if total > 70 else 'moderate'} ESG potential in {region}."
        days_ago = random.randint(0, 180)

        ev = Evaluation(
            name=name, budget=round(budget, 2), co2_reduction=round(co2, 1),
            social_impact=round(social, 1), duration_months=duration,
            total_score=round(total, 2), environment_score=round(env, 2),
            social_score=round(soc, 2), economic_score=round(eco, 2),
            success_probability=round(prob, 3), recommendation=rec,
            risk_level=risk, region=region, lat=round(lat, 4), lon=round(lon, 4),
            created_at=datetime.utcnow() - timedelta(days=days_ago),
        )
        db.add(ev)

    db.commit()
    final = db.query(Evaluation).count()
    print(f"Seeded {len(NAMES)} evaluations. Total: {final}")
    db.close()

if __name__ == "__main__":
    seed()
