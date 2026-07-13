#!/usr/bin/env python3
"""Verify forecast API routes are correctly configured without duplicate prefixes."""

import sys
sys.path.insert(0, '.')

from fastapi import FastAPI
from app.api import forecast as forecast_api

# Simulate registration like in main.py
app = FastAPI()
app.include_router(forecast_api.router, prefix='/api/v1')

print("=" * 70)
print("FORECAST API ROUTES VERIFICATION")
print("=" * 70)

forecast_routes = [r for r in app.routes if hasattr(r, 'path') and '/forecast' in r.path]

if not forecast_routes:
    print("❌ ERROR: No forecast routes found!")
    sys.exit(1)

print(f"\n✅ Found {len(forecast_routes)} forecast routes:\n")

for route in forecast_routes:
    methods = ','.join(sorted(route.methods)) if hasattr(route, 'methods') else 'N/A'
    path = route.path

    # Check for duplicate /forecast/ prefix
    if path.count('/forecast/forecast') > 0:
        print(f"❌ DUPLICATE PREFIX: {methods:10} {path}")
    else:
        print(f"✅ OK: {methods:10} {path}")

print("\n" + "=" * 70)
print("EXPECTED PATHS (with /api/v1 prefix):")
print("=" * 70)

expected_paths = [
    "/api/v1/forecast",
    "/api/v1/forecast/cache",
    "/api/v1/forecast/pretrain",
    "/api/v1/forecast/history",
    "/api/v1/forecast/metrics/performance",
    "/api/v1/forecast/metrics/latest",
]

actual_paths = {r.path for r in forecast_routes}

print("\nExpected paths:")
for path in expected_paths:
    if path in actual_paths:
        print(f"  ✅ {path}")
    else:
        print(f"  ❌ MISSING: {path}")

print("\nUnexpected paths:")
unexpected = actual_paths - set(expected_paths)
if unexpected:
    for path in unexpected:
        print(f"  ⚠️  {path}")
else:
    print("  None - all paths are expected ✅")

print("\n" + "=" * 70)
print("ROUTE DETAILS:")
print("=" * 70)

for route in sorted(forecast_routes, key=lambda r: r.path):
    methods = ','.join(sorted(route.methods)) if hasattr(route, 'methods') else 'N/A'
    print(f"\n{route.path}")
    print(f"  Methods: {methods}")
    if hasattr(route, 'name'):
        print(f"  Handler: {route.name}")
    if hasattr(route, 'endpoint'):
        print(f"  Function: {route.endpoint.__module__}.{route.endpoint.__name__}")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

duplicates = [r for r in forecast_routes if '/forecast/forecast' in r.path]
if duplicates:
    print(f"\n❌ FOUND {len(duplicates)} ROUTES WITH DUPLICATE PREFIX!")
    print("   These routes will result in /api/v1/forecast/forecast/... URLs")
    sys.exit(1)
else:
    print(f"\n✅ ALL {len(forecast_routes)} ROUTES ARE CORRECT")
    print("   No duplicate /forecast/ prefixes found")
    print("   All paths follow the pattern: /api/v1/forecast/...")

print("\n" + "=" * 70)
