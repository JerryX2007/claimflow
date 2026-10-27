"""Fixed reference data: service categories, typical fees and plan designs.

Fee ranges are rough Ontario-style figures for realism, not authoritative prices.
"""

from dataclasses import dataclass

CATEGORIES = [
    "physiotherapy",
    "massage_therapy",
    "chiropractic",
    "psychology",
    "dental_cleaning",
    "vision",
]

# Typical fee per visit, in cents (low, high).
TYPICAL_FEE_CENTS = {
    "physiotherapy": (9_000, 13_000),
    "massage_therapy": (8_000, 12_500),
    "chiropractic": (6_000, 9_500),
    "psychology": (17_000, 24_000),
    "dental_cleaning": (15_000, 32_000),
    "vision": (20_000, 45_000),
}

# An amount above this multiple of the typical high fee is flagged for review.
ANOMALY_MULTIPLIER = 2.5

# How each category appears on a receipt.
SERVICE_DESCRIPTIONS = {
    "physiotherapy": ["Physiotherapy treatment", "Physiotherapy - follow-up visit",
                      "Physio initial assessment", "Physiotherapy session (45 min)"],
    "massage_therapy": ["Registered massage therapy (60 min)", "Massage therapy - 45 min",
                        "RMT treatment", "Therapeutic massage (90 min)"],
    "chiropractic": ["Chiropractic adjustment", "Chiropractic treatment",
                     "Chiropractic initial exam", "Spinal adjustment"],
    "psychology": ["Psychotherapy session (50 min)", "Individual counselling",
                   "Psychological services", "Therapy session"],
    "dental_cleaning": ["Scaling and polishing", "Dental hygiene recall",
                        "Cleaning and exam", "Periodontal scaling (2 units)"],
    "vision": ["Prescription eyeglasses", "Contact lenses (12-month supply)",
               "Eyeglass frames and lenses", "Prescription sunglasses"],
}

# Practitioner credential and registration-number prefix per category.
CREDENTIALS = {
    "physiotherapy": ("PT", "PT"),
    "massage_therapy": ("RMT", "MT"),
    "chiropractic": ("DC", "DC"),
    "psychology": ("C.Psych.", "PSY"),
    "dental_cleaning": ("RDH", "RDH"),
    "vision": ("Optician", "OPT"),
}

CLINIC_SUFFIXES = {
    "physiotherapy": ["Physiotherapy", "Physio & Rehab", "Sports Physiotherapy", "Rehab Centre"],
    "massage_therapy": ["Massage Therapy", "Wellness Clinic", "Therapeutic Massage", "Body Works"],
    "chiropractic": ["Chiropractic", "Spine & Wellness", "Family Chiropractic", "Chiropractic Centre"],
    "psychology": ["Psychology Group", "Counselling Services", "Mental Health Clinic", "Therapy Centre"],
    "dental_cleaning": ["Dental", "Family Dentistry", "Dental Centre", "Smile Dental"],
    "vision": ["Optical", "Eyewear", "Vision Centre", "Optometry & Eyewear"],
}


@dataclass(frozen=True)
class CoverageRule:
    category: str
    coinsurance_pct: int           # share the plan pays
    per_visit_max_cents: int | None
    annual_max_cents: int
    requires_registered_provider: bool = True


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    sponsor: str
    rules: tuple[CoverageRule, ...]

    def rule_for(self, category: str) -> CoverageRule | None:
        return next((r for r in self.rules if r.category == category), None)


PLANS = [
    Plan("BASIC", "Basic Health", "Maple Logistics Inc.", (
        CoverageRule("physiotherapy", 80, 6_000, 50_000),
        CoverageRule("massage_therapy", 80, 5_000, 30_000),
        CoverageRule("chiropractic", 80, 4_000, 30_000),
    )),
    Plan("STANDARD", "Standard Health & Dental", "Lakeshore Software Ltd.", (
        CoverageRule("physiotherapy", 80, 8_000, 75_000),
        CoverageRule("massage_therapy", 80, 8_000, 50_000),
        CoverageRule("chiropractic", 80, 6_000, 50_000),
        CoverageRule("psychology", 80, 15_000, 100_000),
        CoverageRule("dental_cleaning", 80, None, 150_000),
    )),
    Plan("ENHANCED", "Enhanced Health, Dental & Vision", "Northbridge Engineering Corp.", (
        CoverageRule("physiotherapy", 100, None, 100_000),
        CoverageRule("massage_therapy", 90, None, 75_000),
        CoverageRule("chiropractic", 90, None, 75_000),
        CoverageRule("psychology", 100, 20_000, 200_000),
        CoverageRule("dental_cleaning", 90, None, 250_000),
        # Annual max must exceed the largest typical single claim ($450 eyeglasses),
        # otherwise an ordinary claim would already be a partial payment.
        CoverageRule("vision", 100, None, 50_000),
    )),
]

PLANS_BY_ID = {p.plan_id: p for p in PLANS}
