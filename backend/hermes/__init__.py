"""
Hermes orchestrator.

The LLM "brain" that synthesizes signals from the agent layer into
recommendations. The LLM never constructs squads directly — it emits
bounded per-player adjustments that feed the existing MILP optimizers
(the "MILP firewall").
"""
