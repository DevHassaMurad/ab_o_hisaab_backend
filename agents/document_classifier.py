"""Explainable content hints. This is a rule-based classifier, not a trained model."""

KEYWORDS = {
    "cnic": ["national identity", "identity card", "pakistan", "nadra", "cnic"],
    "passport": ["passport", "nationality", "date of birth", "place of birth", "p<"],
    "home_address": [
        "address",
        "utility",
        "electricity",
        "bill",
        "tenancy",
        "lease",
        "consumer",
    ],
    "ngo_registration": [
        "certificate",
        "registration",
        "organization",
        "organisation",
        "society",
        "trust",
        "registered",
    ],
}


def classify(text):
    lowered = text.lower()
    matches = {
        kind: [term for term in terms if term in lowered]
        for kind, terms in KEYWORDS.items()
    }
    scores = {kind: len(terms) for kind, terms in matches.items()}
    predicted = max(scores, key=scores.get)
    return {
        "predicted_type": predicted if scores[predicted] else None,
        "evidence_counts": scores,
    }
