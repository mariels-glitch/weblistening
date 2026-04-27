"""
Theme tagger. Controlled vocabulary (PRD §6.2). Keyword-based for V1 so
we stay explainable and deterministic; LLM-assisted refinement can come in V2.

Word-boundary matching is used rather than substring so "application" doesn't
fire the "app" keyword for App UX. Multi-word phrases are checked as substrings.
"""
from __future__ import annotations

import re


# theme_id: (display_name, [keyword or phrase])
THEMES: dict[str, tuple[str, list[str]]] = {
    "onboarding": (
        "Onboarding / Application",
        ["approved", "application", "apply", "sign up", "signed up", "2 minutes", "fast approval", "credit pull", "hard pull", "instant approval"],
    ),
    "rewards": (
        "Rewards / 3x pet category",
        ["3x", "rewards", "points", "cashback", "cash back", "earned", "petsmart", "chewy", "miscategorized", "miscategorization"],
    ),
    "claims_speed": (
        "Claims speed",
        ["reimbursed", "reimbursement", "paid out", "still waiting", "weeks", "months", "days to pay", "quick claim", "slow claim", "claim processing"],
    ),
    "claims_coverage": (
        "Claims coverage / denials",
        ["denied", "denial", "pre-existing", "preexisting", "excluded", "not covered", "coverage gap", "deductible"],
    ),
    "support": (
        "Customer support",
        ["support", "agent", "called", "no one answered", "rude", "helpful", "chat", "email response", "phone"],
    ),
    "app_ux": (
        "App UX / bugs",
        ["app", "crash", "crashed", "bug", "glitch", "freezes", "upload", "photo", "face id", "login"],
    ),
    "pricing": (
        "Pricing / APR",
        ["annual fee", "apr", "interest rate", "expensive", "cheap", "price", "cost"],
    ),
    "trust": (
        "Trust / legitimacy",
        ["scam", "legit", "legitimate", "trusted", "shady", "sketchy", "real company"],
    ),
    "competitor_compare": (
        "Competitor comparison",
        ["vs lemonade", "vs chase", "vs capital one", "better than", "worse than", "switched from", "switched to"],
    ),
    "pet_story": (
        "Pet story / testimonial",
        ["my dog", "my cat", "my pup", "my kitten", "my puppy", "when my pet", "saved"],
    ),
}


def _match(keyword: str, text: str) -> bool:
    """Word-boundary match for single words; substring for multi-word phrases."""
    if " " in keyword:
        return keyword in text
    # \b word boundary; keyword is already lowercase; text should be too
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def tag(text: str) -> list[str]:
    tl = text.lower()
    hits: list[str] = []
    for theme_id, (_label, kws) in THEMES.items():
        if any(_match(k, tl) for k in kws):
            hits.append(theme_id)
    return hits


def label(theme_id: str) -> str:
    return THEMES.get(theme_id, (theme_id, []))[0]
