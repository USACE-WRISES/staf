"""EASI — Ecosystem Assessment Screening Index automation.

A Shiny-for-Python app that delineates a watershed + reach from a clicked point
and auto-computes/scores the 20 EASI screening metrics from national public data.

``EASI_METHOD_PACKAGE`` (a method package zip or folder, see ``method_package``)
selects the scoring method for this process. It is resolved here, before
``easi.config`` reads its data folder, so the unchanged scoring code runs on the
package's files; unset, the built-in method in ``data/`` is used.
"""

__version__ = "0.0.1"

from .method_package import materialize_from_env as _materialize_from_env

_materialize_from_env()
del _materialize_from_env
