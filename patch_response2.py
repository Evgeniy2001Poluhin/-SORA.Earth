with open("app/main.py", "r") as f:
    src = f.read()

old = '"success_probability": success_prob,\n        "recommendations": recommendations,'
new = '"success_probability": success_prob,\n        "success_probability_v2": success_prob_v2,\n        "recommendations": recommendations,'

if old in src:
    src = src.replace(old, new)
    print("Patch OK")
else:
    print("Not found, trying without trailing comma context:")
    # fallback: просто найти строку
    old2 = '"success_probability": success_prob,'
    new2 = '"success_probability": success_prob,\n        "success_probability_v2": success_prob_v2,'
    if old2 in src:
        src = src.replace(old2, new2, 1)  # только первое вхождение
        print("Fallback patch OK")
    else:
        print("FAILED")

with open("app/main.py", "w") as f:
    f.write(src)
