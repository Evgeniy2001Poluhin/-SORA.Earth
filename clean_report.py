from pathlib import Path
import json
html = Path("reports/shap_report.html").read_text(encoding="utf-8")
marker = "<h2>Model Performance Metrics</h2>"
first = html.find(marker)
if first != -1:
    html = html[:first]
    if html.rstrip().endswith("<hr>"):
        html = html.rstrip()[:-4]
    html = html.rstrip()
html = html + "\n</body></html>"
Path("reports/shap_report.html").write_text(html, encoding="utf-8")
print("Cleaned. Blocks remaining:", html.count(marker))
