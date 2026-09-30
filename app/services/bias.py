"""B1 — detect biased or exclusionary wording in vacancy texts (RU/KK/EN).

Two layers:
1. A deterministic lexicon (instant, free, testable) for the most common discriminatory
   demands: age, gender, appearance, family/marital status, ethnicity, religion, health.
2. An optional Gemini pass that catches subtler wording, explains each finding and
   proposes a neutral rewrite. AI findings whose phrase is not literally in the text are
   discarded (no hallucinated quotes).
Findings are merged, de-duplicated by overlapping text, and sorted by position.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from markupsafe import Markup, escape

from .gemini import LANGUAGE_NAMES, GeminiError, GeminiService, fence, load_prompt
from .schemas import BiasReport

log = logging.getLogger(__name__)

_F = re.IGNORECASE | re.UNICODE


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    category: str
    severity: str = "high"


def _rules(category: str, severity: str, *patterns: str) -> list[Rule]:
    return [Rule(re.compile(p, _F), category, severity) for p in patterns]


RULES: list[Rule] = [
    # Age
    *_rules("age", "high",
            r"\b(?:до|не старше|моложе)\s+\d{2}\s*(?:-?\s*(?:ти|х|ми))?\s*(?:лет|года)\b",
            r"\bв возрасте\s+(?:от|до)?\s*\d{2}",
            r"\bвозраст\s*(?::|-|–|до|от)\s*\d{2}",
            r"\b\d{2}\s*(?:-\s*\d{2}\s*)?жас(?:қа|ка)\s*дейін\w*",
            r"\bжасы\s+\d{2}",
            r"\b(?:under|below|not older than)\s+\d{2}\b",
            r"\baged?\s+\d{2}\s*(?:-|to)\s*\d{2}\b"),
    *_rules("age", "medium",
            r"\bмолод(?:ой|ая|ые|ых|ого|ому|ую|ом)\s+(?:и\s+\w+\s+)?(?:коллектив|команд\w*|специалист\w*|сотрудник\w*)",
            r"\bжас\s+(?:ұжым\w*|маман\w*|қызметкер\w*)",
            r"\b(?:young|youthful)\s+(?:team|and|professional|candidate)\w*",
            r"\bdigital native\b",
            r"\brecent graduates? only\b"),
    # Gender
    *_rules("gender", "high",
            r"\bдевушк(?:а|и|у|е|ам)\b",
            r"\b(?:только\s+)?(?:женщин(?:а|ы)?|мужчин(?:а|ы)?)\b",
            r"\bпарн(?:и|я|ей)\b",
            r"\b(?:женский|мужской)\s+пол\b",
            r"\bқыз(?:дар|ды|дарды)?\b",
            r"\bжігіт(?:тер|терді)?\b",
            r"\b(?:ер|әйел)\s+(?:адам|азамат)\w*",
            r"\b(?:male|female)s?\s+(?:only|candidate|applicant)s?\b",
            r"\bgirls?\b"),
    *_rules("gender", "medium", r"\b(?:salesman|chairman|guys|продавщиц\w*|секретарш\w*|уборщиц\w*)\b"),
    # Appearance
    *_rules("appearance", "high",
            r"\b(?:приятн\w*|привлекательн\w*)\s+внешност\w*",
            r"\bвнешност(?:ь|и|ью)\b",
            r"\bрост(?:ом)?\s+(?:от|не ниже)\s+\d{3}",
            r"\bрезюме\s+с\s+фото\w*|\bфото\w*\s+(?:к|в)\s+резюме\b|\bприложите\s+фото\w*",
            r"\bсырт(?:қы)?\s+келбет\w*|\bкелбетт\w*",
            r"\b(?:attractive|good[- ]looking|photo required)\b"),
    *_rules("appearance", "medium", r"\bпрезентабельн\w*", r"\bстройн(?:ая|ую)\b"),
    # Family and marital status
    *_rules("family", "high",
            r"\bбез детей\b",
            r"\b(?:не\s+)?(?:замужем|замужняя|женат(?:ый)?|холост\w*)\b",
            r"\bсемейн(?:ое|ым|ого)\s+положени\w*",
            r"\bне\s+планирующ\w*\s+(?:декрет|детей|беременност)\w*",
            r"\bбалалары?\s+жоқ\b",
            r"\bтұрмыс(?:қа)?\s+(?:шықпаған|құрмаған)\b",
            r"\bүйленбеген\b",
            r"\b(?:no children|unmarried|marital status)\b"),
    # Ethnicity / nationality
    *_rules("ethnicity", "high",
            r"\bславянск\w*",
            r"\bнациональност\w*",
            r"\bтолько\s+(?:казах\w*|русск\w*)",
            r"\bұлты\b",
            r"\bтек\s+қазақ\w*",
            r"\b(?:nationality|ethnicity)\b"),
    *_rules("ethnicity", "medium",
            r"\bтолько\s+граждан\w*",
            r"\bnative speakers?\b",
            r"\bносител\w*\s+(?:русского|казахского|английского)\s+языка\b"),
    # Religion
    *_rules("religion", "high", r"\bрелиги\w*|\bвероисповедани\w*|\bправославн\w*|\bмусульман\w*|\bмұсылман\w*|\bдіни\b|\breligio\w*"),
    # Health
    *_rules("health", "medium",
            r"\bбез вредных привычек\b",
            r"\b(?:отличное|крепкое)\s+здоровье\b",
            r"\bзиянды\s+әдет\w*\s+жоқ\w*|\bзиянды\s+әдеттерсіз\b",
            r"\b(?:no bad habits|perfect health)\b"),
]


# Inclusive phrasing ("мужчины и женщины", "ер мен әйел", "men and women") is not bias.
INCLUSIVE = [
    re.compile(p, _F)
    for p in (
        r"\b(?:мужчин\w*|женщин\w*|девушк\w*|юнош\w*|парн\w*)\s+(?:и|или)\s+(?:мужчин\w*|женщин\w*|девушк\w*|юнош\w*|парн\w*)",
        r"\b(?:ер|әйел|қыз|жігіт)\w*\s+(?:мен|және|немесе)\s+(?:ер|әйел|қыз|жігіт)\w*",
        r"\b(?:men|women|male|female)\s+(?:and|or)\s+(?:men|women|male|female)\b",
        r"\bнезависимо от\s+(?:пола|возраста|национальности)[^.;\n]*",
        r"\b(?:regardless of|any)\s+(?:gender|age)\b",
    )
]


@dataclass
class Finding:
    phrase: str
    category: str
    severity: str
    start: int
    end: int
    source: str  # "rule" | "ai"
    explanation: str = ""
    suggestion: str = ""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def rule_findings(text: str) -> list[Finding]:
    inclusive = [(m.start(), m.end()) for p in INCLUSIVE for m in p.finditer(text)]
    found = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            if any(s <= match.start() and match.end() <= e for s, e in inclusive):
                continue
            found.append(Finding(match.group(0), rule.category, rule.severity, match.start(), match.end(), "rule"))
    return found


def _locate(text: str, phrase: str) -> tuple[int, int] | None:
    phrase = phrase.strip().strip("«»\"'")
    if len(phrase) < 2:
        return None
    index = text.lower().find(phrase.lower())
    return (index, index + len(phrase)) if index >= 0 else None


def _merge(findings: list[Finding]) -> list[Finding]:
    """Keep one finding per overlapping span: AI (has explanation) beats rule, high beats medium, longer wins."""
    rank = lambda f: (f.source == "ai", f.severity == "high", f.end - f.start)  # noqa: E731
    kept: list[Finding] = []
    for finding in sorted(findings, key=rank, reverse=True):
        if not any(finding.start < k.end and k.start < finding.end for k in kept):
            kept.append(finding)
    return sorted(kept, key=lambda f: f.start)


def check(text: str, language: str = "ru", service: GeminiService | None = None) -> dict[str, Any]:
    """Return a JSON-serializable report. Uses Gemini when a service is given."""
    findings = rule_findings(text)
    rewrite, ai_checked = "", False
    if service is not None:
        try:
            report = _ai_report(service, text, language)
            ai_checked = True
            rewrite = report.rewrite.strip()
            for item in report.findings:
                span = _locate(text, item.phrase)
                if span is None:
                    log.info("Dropping AI bias finding not present in text")
                    continue
                findings.append(Finding(text[span[0]:span[1]], item.category, item.severity, *span, "ai",
                                        item.explanation.strip(), item.suggestion.strip()))
        except GeminiError as exc:
            log.warning("AI bias check unavailable: %s", exc)
    merged = _merge(findings)
    return {
        "text_hash": text_hash(text),
        "ai_checked": ai_checked,
        "rewrite": rewrite if merged else "",
        "findings": [asdict(f) for f in merged],
        "counts": {"high": sum(f.severity == "high" for f in merged), "medium": sum(f.severity == "medium" for f in merged)},
    }


def _ai_report(service: GeminiService, text: str, language: str) -> BiasReport:
    system = load_prompt("bias_system").substitute(language=LANGUAGE_NAMES.get(language, "Russian"))
    user = load_prompt("bias_user").substitute(vacancy=fence(text, "vacancy"))
    return service.generate(system, user, BiasReport)


def localize(report: dict[str, Any], t) -> dict[str, Any]:
    """Add translated labels and default explanations (rule findings have none of their own)."""
    findings = [
        {
            **f,
            "label": t(f"bias.cat.{f['category']}"),
            "severity_label": t(f"bias.severity.{f['severity']}"),
            "explanation": f["explanation"] or t(f"bias.why.{f['category']}"),
            "suggestion": f["suggestion"] or t(f"bias.fix.{f['category']}"),
        }
        for f in report["findings"]
    ]
    return {**report, "findings": findings}


def highlight(text: str, findings: list[dict[str, Any]]) -> Markup:
    """Escape the vacancy text and wrap each finding in <mark>. Safe for templates."""
    out, cursor = [], 0
    for f in sorted(findings, key=lambda f: f["start"]):
        start, end = f["start"], f["end"]
        if start < cursor or end > len(text):
            continue
        out.append(escape(text[cursor:start]))
        css = "bias bias--high" if f["severity"] == "high" else "bias"
        out.append(Markup('<mark class="{}">{}</mark>').format(css, text[start:end]))
        cursor = end
    out.append(escape(text[cursor:]))
    return Markup("").join(out)
