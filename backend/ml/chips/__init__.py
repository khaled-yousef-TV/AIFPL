"""
Chip optimization module for FPL chips.

This module provides optimization algorithms for:
- Triple Captain (TC): Monte Carlo simulation for haul probability
- Bench Boost (BB): MILP optimization for 15-man squad
- Wildcard (WC): 8-GW optimization with LSTM+XGBoost overlay
"""

from .haul_probability import HaulProbabilityCalculator
from .triple_captain import TripleCaptainOptimizer

__all__ = [
    "TripleCaptainOptimizer",
    "HaulProbabilityCalculator",
]

