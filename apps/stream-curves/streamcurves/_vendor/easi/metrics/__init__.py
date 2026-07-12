"""Per-metric adapters: each turns fetched data into a value -> rating -> index.

Each adapter implements the MetricAdapter protocol (see ``base.py``) and is
registered by metricId so the orchestrator can fan out over all 20 EASI metrics.
"""
