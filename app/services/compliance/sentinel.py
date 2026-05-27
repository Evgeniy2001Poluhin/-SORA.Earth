"""Compliance Sentinel: regex-based PII + bias + policy guard."""
from __future__ import annotations
import re, logging
from typing import List, Dict, Any

log = logging.getLogger(__name__)

_PII_PATTERNS = [
    ("EMAIL_ADDRESS", r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", 0.95),
    ("PHONE_NUMBER", r"\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}", 0.85),
    ("US_SSN", r"\b\d{3}-\d{2}-\d{4}\b", 0.95),
    ("CREDIT_CARD", r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", 0.90),
    ("IBAN", r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b", 0.85),
    ("IP_ADDRESS", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", 0.75),
    ("URL", r"https?://[^\s]+", 0.70),
]

_BIAS_PATTERNS = [
    (r"\b(young|old)\s+(applicant|founder|borrower)\b", "age_stereotype",
     "Age-based descriptor may introduce bias"),
    (r"\b(african|asian|hispanic|caucasian)\s+(applicant|borrower|founder)?\b", "race_descriptor",
     "Race descriptor detected; verify relevance to ESG metric"),
    (r"\bdeveloping\s+countries?\b", "global_south_label",
     "Consider 'Global South' or specific country names instead"),
    (r"\bguaranteed?\b(?!\s+(by|loan|principal))", "certainty_claim",
     "Avoid certainty language; ML predictions are probabilistic"),
    (r"\bwill\s+(succeed|fail)\b", "outcome_promise",
     "Replace deterministic claim with probability statement"),
    (r"\bfavors?\s+(only\s+)?(male|female|men|women)\b", "gender_preference",
     "Gender preference detected; ensure non-discriminatory criteria"),
    (r"\b(only|exclusively)\s+(male|female|men|women)\b", "gender_exclusion",
     "Gender exclusion violates equal opportunity principles"),
    (r"\b(male|female)-only\b", "gender_exclusion",
     "Gender-only restriction detected"),
    (r"\bprefers?\s+(men|women|male|female)\s+(for|in|as)\b", "gender_role_bias",
     "Gendered role assignment may indicate stereotyping"),
]

_POLICY_PATTERNS = [
    (r"\bfunding\s+approved\b", "funding_promise",
     "Co-Pilot must not state funding approval; final decision is human"),
    (r"\bAI\s+recommends\s+(approval|rejection)\b", "binding_recommendation",
     "Position AI as advisor, not decision maker"),
    (r"\b100%\s+(certain|sure|guaranteed)\b", "absolute_claim",
     "Absolute certainty claim; remove or soften"),
]


def _scan_pii(text):
    out = []
    for entity, pat, conf in _PII_PATTERNS:
        for m in re.finditer(pat, text):
            out.append({"type": entity, "text": m.group(0), "start": m.start(),
                        "end": m.end(), "confidence": conf})
    out.sort(key=lambda x: x["start"])
    cleaned = []
    for f in out:
        if cleaned and f["start"] < cleaned[-1]["end"]:
            if f["confidence"] > cleaned[-1]["confidence"]:
                cleaned[-1] = f
            continue
        cleaned.append(f)
    return cleaned


def _scan_patterns(text, patterns):
    out = []
    for pat, code, note in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            out.append({"pattern": code, "match": m.group(0), "start": m.start(),
                        "end": m.end(), "note": note})
    return out


def _redact(text, pii):
    if not pii:
        return text
    sorted_pii = sorted(pii, key=lambda x: x["start"], reverse=True)
    out = text
    for p in sorted_pii:
        out = out[:p["start"]] + f"[{p['type']}]" + out[p["end"]:]
    return out


def _risk_score(pii, bias, policy):
    pii_w = sum(p["confidence"] for p in pii) * 0.4
    bias_w = len(bias) * 0.15
    policy_w = len(policy) * 0.5
    return round(min(1.0, pii_w + bias_w + policy_w), 3)


def check(text):
    if not text or not text.strip():
        return {"passed": True, "pii_findings": [], "bias_findings": [],
                "policy_violations": [], "redacted_text": text, "risk_score": 0.0,
                "engine": "regex-v1"}
    pii = _scan_pii(text)
    bias = _scan_patterns(text, _BIAS_PATTERNS)
    policy = _scan_patterns(text, _POLICY_PATTERNS)
    risk = _risk_score(pii, bias, policy)
    return {
        "passed": risk < 0.5 and not policy,
        "pii_findings": pii, "bias_findings": bias, "policy_violations": policy,
        "redacted_text": _redact(text, pii), "risk_score": risk, "engine": "regex-v1",
    }
