"""
Proposer Agent Sub-modules.

This package contains the modular components of the proposer coding agent:
- diagnosis: Stage 1 - Error analysis
- plan: Stage 2 - Plan generation with to-do tracking
- apply: Stage 3 - Apply changes with version control
- verifier: Stage 4 - Verification and testing
"""

from .diagnosis import DiagnosisModule
from .plan import PlanModule
from .apply import ApplyModule
from .verifier import VerifierModule
from .appworld_diagnosis import AppWorldDiagnosisModule

__all__ = [
    'DiagnosisModule',
    'PlanModule', 
    'ApplyModule',
    'VerifierModule',
    'AppWorldDiagnosisModule'
]

