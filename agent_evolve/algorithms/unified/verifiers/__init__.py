"""Unified verifier atoms — inspect workspace + reports and emit a Verdict.

Every module here is an **independent reimplementation** of legacy logic,
with no ``import`` from the legacy engine packages.
"""

from . import no_verify, stagnation_rollback

__all__: list[str] = []
