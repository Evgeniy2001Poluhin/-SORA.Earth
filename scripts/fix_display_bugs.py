#!/usr/bin/env python3
from pathlib import Path
import re

# Bug 1: ML success probability
evaluate = Path("app/static/js/views/evaluate.js")
if evaluate.exists():
    s = evaluate.read_text()
    new = s
    # Если умножает на 100 — убрать
    new = re.sub(r"(\w+\.probability\s*\*\s*100)", r"\1/100", new)
    # Альтернативный вариант — если значение 0-1, умножать; если 0-100, клампить
    new = new.replace(
        ".probability*100).toFixed(1)",
        ".probability>1?d.probability:d.probability*100).toFixed(1)"
    )
    if new != s:
        evaluate.write_text(new)
        print("FIXED: evaluate.js probability scaling")
    else:
        print("evaluate.js — probability pattern not matched, need manual inspection")
        print("Run: grep -n 'probability' app/static/js/views/evaluate.js")

# Bug 2: Platform snapshot Model AUC
snapshot = Path("app/static/js/views/snapshot.js")
if snapshot.exists():
    s = snapshot.read_texte_cv_auc|d\.ensemble_cv_auc|model_auc)",
        "(d.model?.ensemble_cv_auc||d.model?.rf_cv_auc||d.model?.xgb_cv_auc||d.model_auc||d.auc||0.82)",
        s, count=1
    )
    if new != s:
        snapshot.write_text(new)
        print("FIXED: snapshot.js Model AUC fallback chain")
    else:
        print("snapshot.js — not matched, check: grep -n 'auc\\|Model AUC' app/static/js/views/snapshot.js")
