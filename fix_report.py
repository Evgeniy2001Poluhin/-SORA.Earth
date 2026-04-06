from pathlib import Path
html = Path("reports/shap_report.html").read_text(encoding="utf-8")
first = html.find("<h2>Model Performance Metrics</h2>")
second = html.find("<h2>Model Performance Metrics</h2>", first + 1)
div_start = html.rfind("<div", 0, first)
hr_before_new = html.rfind("<hr>", 0, second)
print("Удаляем:", div_start, "->", hr_before_new)
html = html[:div_start] + html[hr_before_new:]
Path("reports/shap_report.html").write_text(html, encoding="utf-8")
count = html.count("<h2>Model Performance Metrics</h2>")
print("Блоков после чистки:", count)
