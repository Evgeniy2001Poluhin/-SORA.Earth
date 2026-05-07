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
        print(f"Already {existing} evaluations, skipping.")
        db.close()
        return
    random.seed(42)
    for i, name in enumerate(NAMES):
        region = REGIONS[i % len(REGIONS)]
        lat, lon = COORDS[region]
        ev = Evaluation(
            name=name, budget=round(random.uniform(50000, 5000000), 2),
            co2_reduction=round(random.uniform(10, 500), 1),
            social_impact=round(random.uniform(1, 10), 1),
            duration_months=random.randint(6, 60),
            total_score=round(random.uniform(30, 95), 2),
            environment_score=round(random.uniform(20, 100), 2),
            social_score=round(random.uniform(20, 100), 2),
            economic_score=round(random.uniform(20, 100), 2),
            success_probability=round(random.uniform(0.3, 0.95), 3),
            recommendation=f"{'Strong' if random.random()>0.5 else 'Moderate'} ESG potential in {region}.",
            risk_level=random.choice(["low", "medium", "high"]),
            region=region, lat=round(lat+random.uniform(-2,2), 4),
            lon=round(lon+random.uniform(-2,2), 4),
            created_at=datetime.utcnow() - timedelta(days=random.randint(0, 180)),
        )
        db.add(ev)
    db.commit()
    print(f"Seeded {len(NAMES)} evaluations. Total: {db.query(Evaluation).count()}")
    db.close()

if __name__ == "__main__":
    seed()
