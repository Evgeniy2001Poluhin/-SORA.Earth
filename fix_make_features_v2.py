with open("app/main.py", "r") as f:
    src = f.read()

bad = "row = [[data.budget, data.cct, data.duration_months,"
good = """row = [[data.budget, data.co2_reduction, data.social_impact, data.duration_months,
            bpm, c2d, eff, ir, be, c_enc, r_enc]]"""

if bad in src:
    src = src.replace(bad, good)
    print("Fixed truncated row")
else:
    print("Not found, showing context:")
    idx = src.find("make_features_v2")
    print(src[idx:idx+600])

with open("app/main.py", "w") as f:
    f.write(src)
