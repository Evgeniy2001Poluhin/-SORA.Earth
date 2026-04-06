with open("app/main.py", "r") as f:
    src = f.read()

# Показываем контекст вокруг success_prob в return/response
import re
for m in re.finditer(r'success_prob', src):
    start = max(0, m.start() - 60)
    end   = min(len(src), m.end() + 80)
    print(f"L~{src[:m.start()].count(chr(10))+1}: {repr(src[start:end])}")
    print()
