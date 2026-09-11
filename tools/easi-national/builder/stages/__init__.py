"""Pipeline stages. Each ``run_*`` is idempotent, writes through temp files,
and checks the operator's control file between requests."""
