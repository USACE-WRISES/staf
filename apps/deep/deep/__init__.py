"""DEEP — Detailed Evaluation of Ecosystem Processes.

The detailed-tier assessment tool of the Stream Tiered Assessment Framework
(STAF), sibling to EASI (screening) and SFARI (rapid). DEEP runs a site's
measured metric values through calibrated reference curves to produce function
scores, Physical/Chemical/Biological outcome sub-indices, and an overall
Ecosystem Condition Index (ECI).

Forked from SFARI: the scoring *rollup* (functions -> outcomes -> ECI) is reused
unchanged, so all three STAF tiers land on one comparable scale. What differs is
the front half — instead of Likert professional judgment, DEEP interpolates each
metric value on its reference curve to get a 0-1 index, then averages a
function's metric indices into a 0-15 function score (see :mod:`deep.curves`).
"""

__version__ = "0.1.0"
