"""
Haul Probability Calculator

Monte Carlo simulation to calculate the probability of a player scoring 15+ points (a "haul").
Uses Poisson distribution for goals and assists, with clean sheet and bonus point calculations.
"""

import logging
from typing import Dict, Any, Optional
import numpy as np
from scipy.stats import poisson

logger = logging.getLogger(__name__)


class HaulProbabilityCalculator:
    """Calculate haul probability (15+ points) using Monte Carlo simulation."""
    
    # FPL points system
    POINTS_PER_GOAL = {
        1: 6,  # Goalkeeper
        2: 6,  # Defender
        3: 5,  # Midfielder
        4: 4,  # Forward
    }
    
    POINTS_PER_ASSIST = 3
    POINTS_PER_CLEAN_SHEET = {
        1: 4,  # Goalkeeper
        2: 4,  # Defender
        3: 1,  # Midfielder
        4: 0,  # Forward
    }
    
    MIN_HAUL_POINTS = 15
    MONTE_CARLO_ITERATIONS = 10000
    
    def __init__(self):
        """Initialize the calculator."""
        pass
    
    def calculate_haul_probability(
        self,
        xg: float,
        xa: float,
        position: int,
        fixture_difficulty: int,
        is_home: bool,
        clean_sheet_prob: float = 0.0,
        bonus_points_base: float = 0.0,
        is_double_gameweek: bool = False
    ) -> Dict[str, Any]:
        """
        Calculate haul probability for a single gameweek.
        
        Args:
            xg: Expected goals (lambda for Poisson)
            xa: Expected assists (lambda for Poisson)
            position: Player position (1=GK, 2=DEF, 3=MID, 4=FWD)
            fixture_difficulty: Fixture difficulty rating (1-5)
            is_home: Whether player is playing at home
            clean_sheet_prob: Probability of clean sheet (for DEF/GK)
            bonus_points_base: Base bonus points expectation
            is_double_gameweek: Whether player has two fixtures this gameweek
            
        Returns:
            Dictionary with haul probability and statistics
        """
        if is_double_gameweek:
            # For DGW, simulate both fixtures and sum points
            return self._calculate_dgw_haul_probability(
                xg, xa, position, fixture_difficulty, is_home,
                clean_sheet_prob, bonus_points_base
            )
        else:
            return self._calculate_single_fixture_haul_probability(
                xg, xa, position, fixture_difficulty, is_home,
                clean_sheet_prob, bonus_points_base
            )
    
    def _calculate_single_fixture_haul_probability(
        self,
        xg: float,
        xa: float,
        position: int,
        fixture_difficulty: int,
        is_home: bool,
        clean_sheet_prob: float,
        bonus_points_base: float
    ) -> Dict[str, Any]:
        """Calculate haul probability for a single fixture."""
        haul_count = 0
        total_points_samples = []
        
        # Adjust xG/xA based on fixture difficulty and home advantage
        difficulty_factor = self._get_difficulty_factor(fixture_difficulty, is_home)
        adjusted_xg = xg * difficulty_factor
        adjusted_xa = xa * difficulty_factor
        
        # Adjust clean sheet probability based on fixture difficulty
        adjusted_cs_prob = clean_sheet_prob * difficulty_factor
        
        for _ in range(self.MONTE_CARLO_ITERATIONS):
            # Sample goals from Poisson distribution
            goals = poisson.rvs(adjusted_xg) if adjusted_xg > 0 else 0
            
            # Sample assists from Poisson distribution
            assists = poisson.rvs(adjusted_xa) if adjusted_xa > 0 else 0
            
            # Sample clean sheet (for DEF/GK)
            clean_sheet = 0
            if position in [1, 2]:  # GK or DEF
                clean_sheet = 1 if np.random.random() < adjusted_cs_prob else 0
            
            # Calculate bonus points (simplified: based on goals, assists, and base)
            bonus_points = self._calculate_bonus_points(
                goals, assists, bonus_points_base, position
            )
            
            # Calculate total points
            points = (
                goals * self.POINTS_PER_GOAL[position] +
                assists * self.POINTS_PER_ASSIST +
                clean_sheet * self.POINTS_PER_CLEAN_SHEET[position] +
                bonus_points
            )
            
            total_points_samples.append(points)
            
            if points >= self.MIN_HAUL_POINTS:
                haul_count += 1
        
        haul_probability = haul_count / self.MONTE_CARLO_ITERATIONS
        
        return {
            "haul_probability": haul_probability,
            "expected_points": np.mean(total_points_samples),
            "median_points": np.median(total_points_samples),
            "p75_points": np.percentile(total_points_samples, 75),
            "p90_points": np.percentile(total_points_samples, 90),
            "haul_count": haul_count,
            "iterations": self.MONTE_CARLO_ITERATIONS
        }
    
    def _calculate_dgw_haul_probability(
        self,
        xg: float,
        xa: float,
        position: int,
        fixture_difficulty: int,
        is_home: bool,
        clean_sheet_prob: float,
        bonus_points_base: float
    ) -> Dict[str, Any]:
        """Calculate haul probability for a double gameweek (two fixtures)."""
        haul_count = 0
        total_points_samples = []
        
        # For DGW, we simulate both fixtures
        # Assume second fixture has similar difficulty (can be improved with actual fixture data)
        difficulty_factor_1 = self._get_difficulty_factor(fixture_difficulty, is_home)
        difficulty_factor_2 = self._get_difficulty_factor(fixture_difficulty, not is_home)  # Assume away for second
        
        for _ in range(self.MONTE_CARLO_ITERATIONS):
            # Fixture 1
            adjusted_xg_1 = xg * difficulty_factor_1
            adjusted_xa_1 = xa * difficulty_factor_1
            goals_1 = poisson.rvs(adjusted_xg_1) if adjusted_xg_1 > 0 else 0
            assists_1 = poisson.rvs(adjusted_xa_1) if adjusted_xa_1 > 0 else 0
            cs_1 = 1 if position in [1, 2] and np.random.random() < clean_sheet_prob * difficulty_factor_1 else 0
            
            points_1 = (
                goals_1 * self.POINTS_PER_GOAL[position] +
                assists_1 * self.POINTS_PER_ASSIST +
                cs_1 * self.POINTS_PER_CLEAN_SHEET[position]
            )
            
            # Fixture 2
            adjusted_xg_2 = xg * difficulty_factor_2
            adjusted_xa_2 = xa * difficulty_factor_2
            goals_2 = poisson.rvs(adjusted_xg_2) if adjusted_xg_2 > 0 else 0
            assists_2 = poisson.rvs(adjusted_xa_2) if adjusted_xa_2 > 0 else 0
            cs_2 = 1 if position in [1, 2] and np.random.random() < clean_sheet_prob * difficulty_factor_2 else 0
            
            points_2 = (
                goals_2 * self.POINTS_PER_GOAL[position] +
                assists_2 * self.POINTS_PER_ASSIST +
                cs_2 * self.POINTS_PER_CLEAN_SHEET[position]
            )
            
            # Bonus points are awarded per gameweek, not per match
            # Calculate once for the entire gameweek based on total goals/assists
            total_goals = goals_1 + goals_2
            total_assists = assists_1 + assists_2
            bonus_points = self._calculate_bonus_points(total_goals, total_assists, bonus_points_base, position)
            
            # Total points = sum of both fixtures + bonus (awarded once per gameweek)
            total_points = points_1 + points_2 + bonus_points
            total_points_samples.append(total_points)
            
            if total_points >= self.MIN_HAUL_POINTS:
                haul_count += 1
        
        haul_probability = haul_count / self.MONTE_CARLO_ITERATIONS
        
        return {
            "haul_probability": haul_probability,
            "expected_points": np.mean(total_points_samples),
            "median_points": np.median(total_points_samples),
            "p75_points": np.percentile(total_points_samples, 75),
            "p90_points": np.percentile(total_points_samples, 90),
            "haul_count": haul_count,
            "iterations": self.MONTE_CARLO_ITERATIONS,
            "is_double_gameweek": True
        }
    
    def _get_difficulty_factor(self, difficulty: int, is_home: bool) -> float:
        """
        Get difficulty adjustment factor.
        
        FDR: 1=easiest, 5=hardest
        Returns multiplier for xG/xA (higher for easier fixtures)
        """
        # Base factors: easier fixtures = higher multiplier
        base_factors = {
            1: 1.3,  # Very easy
            2: 1.15,  # Easy
            3: 1.0,   # Medium
            4: 0.85,  # Hard
            5: 0.7,   # Very hard
        }
        
        factor = base_factors.get(difficulty, 1.0)
        
        # Home advantage adds ~10%
        if is_home:
            factor *= 1.1
        
        return factor
    
    def _calculate_bonus_points(
        self,
        goals: int,
        assists: int,
        base_bonus: float,
        position: int
    ) -> float:
        """
        Calculate bonus points based on goals, assists, and base BPS.
        
        In FPL, bonus points (0-3) are awarded to top 3 players based on BPS.
        This is a simplified model that estimates bonus based on performance.
        """
        # Base bonus from BPS (scaled down - most players get 0-1 bonus)
        bonus = base_bonus * 0.2  # Reduced scaling
        
        # Goals and assists contribute to bonus, but not linearly
        # In reality, bonus is competitive - only top performers get it
        if goals >= 3:
            bonus += 2.5  # Hat-trick almost guarantees 3 bonus
        elif goals == 2:
            bonus += 1.5  # Brace often gets 2-3 bonus
        elif goals == 1:
            bonus += 0.5  # Single goal might get 1 bonus if no one else performs
        
        if assists >= 3:
            bonus += 1.5
        elif assists == 2:
            bonus += 0.8
        elif assists == 1:
            bonus += 0.3
        
        # Cap at 3 (maximum bonus points in FPL)
        bonus = min(bonus, 3.0)
        
        # Round to nearest integer (bonus points are integers: 0, 1, 2, or 3)
        return round(bonus)

