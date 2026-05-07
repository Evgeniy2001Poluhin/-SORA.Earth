#!/usr/bin/env python3
"""Seed evaluations database with sample projects."""
import json, httpx, sys

BASE = "http://localhost:8000"

r = httpx.post(f"{BASE}/auth/login", json={"username": "admin", "password": "sora2026"}, timeout=30)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

with open("/app/data/seed_projects.json") as f:
    projects = json.load(f)

ok = 0
for p in projects:
    r = httpx.post(f"{BASE}/evaluate", json=p, headers=headers, timeout=30)
    if r.status_code == 200:
        ok += 1
        d = r.json()
        print(f"  {p['project_name']}: score={d.get('esg_score','-')}, risk={d.get('risk_level','-')}")
    else:
        print(f"  {p['project_name']}: FAILED {r.status_code}")

print(f"\nSeeded {ok}/{len(projects)} evaluations")
