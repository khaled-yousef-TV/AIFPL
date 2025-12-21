"""
Constants for FPL AI Agent

Centralized constants to avoid magic numbers and strings throughout the codebase.
"""


class PlayerStatus:
    """Player availability status codes from FPL API."""
    AVAILABLE = "a"
    DOUBTFUL = "d"
    INJURED = "i"
    SUSPENDED = "s"
    UNAVAILABLE = "u"
    NOT_AVAILABLE = "n"


class PlayerPosition:
    """Player position IDs from FPL API."""
    GK = 1  # Goalkeeper
    DEF = 2  # Defender
    MID = 3  # Midfielder
    FWD = 4  # Forward


class RotationRiskLevel:
    """European rotation risk levels."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Squad constraints
MAX_PLAYERS_PER_TEAM = 3
SQUAD_SIZE = 15
STARTING_XI_SIZE = 11
BENCH_SIZE = 4

# Position limits
MAX_GK = 2
MAX_DEF = 5
MAX_MID = 5
MAX_FWD = 3

# Minimum players required
MIN_GK = 1
MIN_DEF = 3
MIN_MID = 3
MIN_FWD = 1

