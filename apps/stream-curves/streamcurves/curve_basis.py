"""What a reference curve rests on, and what each basis is allowed to claim.

Rules REF-08, REF-09, REF-10 and CONF-03 (methodology 0.13, owner decision
2026-09-21).

Every curve this system publishes estimates a reference expectation from
something. Until now the something was assumed: the bundle said "least-disturbed
stations of this ecoregion" whether the stations came from the ecoregion, from
its Level I parent, or from a fixed published threshold that involves no stations
at all. Interior Plateau holds three least-disturbed stations and Eastern Corn
Belt Plains holds none, so for those two regions the assumed sentence was simply
untrue.

This module names the four bases, orders them, labels them, and says what each
one may claim and how much confidence it may carry.

Tokens are immutable. They ride in bundles, digests, manifests and the rule
catalog, so they are never renamed; the display labels may change freely
(guardrail 13, the same contract the engine tokens carry).
"""

from __future__ import annotations

from typing import Optional

#: a pool of least-disturbed stations from the region or a parent ecoregion
REGIONAL = "regional-reference"
#: comparable least-disturbed stations drawn from the national pool
NATIONAL = "national-reference"
#: a fitted stressor-response expectation, validated before it may score
MODELED = "modeled-reference"
#: an external published criterion, verified for fitness rather than agreement
PUBLISHED = "published-benchmark"

#: ladder order: the first rung that admits a metric wins
ORDER = (REGIONAL, NATIONAL, MODELED, PUBLISHED)
ALL = frozenset(ORDER)

LABELS = {
    REGIONAL: "Regional reference",
    NATIONAL: "National reference",
    MODELED: "Modeled reference",
    PUBLISHED: "Published benchmark",
}

#: CONF-02 caps keyed by basis (CONF-03). None means this basis imposes no cap
#: of its own; the borrowed-reference caps still apply on top by transfer risk.
CAPS = {
    REGIONAL: None,
    NATIONAL: 79,
    MODELED: 59,
    PUBLISHED: 59,
}

#: why each cap is what it is, recorded so a reader is not left guessing
CAP_REASONS = {
    NATIONAL: "national_reference",
    MODELED: "modeled_reference",
    PUBLISHED: "published_benchmark",
}

#: one sentence per basis, for the bundle, the report, the card and the
#: calculator. These are user-visible: no em dashes, and no claim that stations
#: came from the ecoregion unless they did.
STATEMENTS = {
    REGIONAL: ("Reference curve fitted to least-disturbed stations selected by a fixed "
               "landscape-pressure screen."),
    NATIONAL: ("Reference curve fitted to comparable least-disturbed stations drawn from the "
               "national pool, because this ecoregion and its parents hold too few of their own."),
    MODELED: ("Reference curve from a modeled expectation. The response to landscape pressure "
              "is estimated nationally and this ecoregion's own level is added, because the "
              "ecoregion holds too few streams clean enough to observe reference condition "
              "directly."),
    PUBLISHED: ("Scored against a published criterion rather than against stations from this "
                "ecoregion. The criterion carries its own definition of reference, which need "
                "not match this assessment's."),
}

#: what a basis does NOT claim, carried beside the statement where it matters
LIMITS = {
    NATIONAL: ("The donor stations lie outside this ecoregion and its parents, matched on natural "
               "setting rather than drawn from it."),
    MODELED: ("Too few streams in this ecoregion are clean enough to observe the reference "
              "condition directly, so the expectation is an extrapolation and not a "
              "measurement."),
    PUBLISHED: ("A published criterion is not an estimate of this ecoregion's reference condition "
                "and may disagree with a reference curve where one exists."),
}


def label_for(basis: Optional[str]) -> str:
    """The display label, or an empty string for an unknown or missing basis."""
    return LABELS.get(str(basis or ""), "")


def cap_for(basis: Optional[str]) -> Optional[int]:
    """The CONF-03 confidence ceiling this basis imposes, or None."""
    return CAPS.get(str(basis or ""))


def statement_for(basis: Optional[str]) -> str:
    return STATEMENTS.get(str(basis or ""), "")


def limit_for(basis: Optional[str]) -> str:
    return LIMITS.get(str(basis or ""), "")


def rank(basis: Optional[str]) -> int:
    """Position on the ladder; an unknown basis sorts last."""
    key = str(basis or "")
    return ORDER.index(key) if key in ALL else len(ORDER)


def from_legacy(criteria_basis: Optional[str]) -> str:
    """The basis a bundle written before this module implies.

    Published versions up to and including Northeastern Highlands v7, Interior
    Plateau v4 and Eastern Corn Belt Plains v6 carry only ``criteriaBasis``,
    which is ``"fixed"`` for the five EASI screening thresholds and
    ``"reference"`` for everything else. The fixed five are published criteria
    and always were, so they map to the published rung rather than to a rung of
    their own.
    """
    return PUBLISHED if str(criteria_basis or "") == "fixed" else REGIONAL


def resolve(basis: Optional[str] = None, *, criteria_basis: Optional[str] = None) -> str:
    """The basis to use, preferring an explicit one and falling back to legacy."""
    key = str(basis or "")
    return key if key in ALL else from_legacy(criteria_basis)
