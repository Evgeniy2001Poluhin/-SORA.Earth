with open("app/main.py", "r") as f:
    src = f.read()

old = '"probability": '
# Найдём полный return-блок вокруг него
import re
m = re.search(r'"probability": (\w+),', src)
if m:
    prob_var = m.group(1)
    print(f"Found: probability var = {prob_var}")

    old_line = f'"probability": {prob_var},'
    new_line = f'"probability": {prob_var},\n        "probability_v2": success_prob_v2,'
    src = src.replace(old_line, new_line, 1)
    print("Patch OK")
else:
    print("Not found")

with open("app/main.py", "w") as f:
    f.write(src)
