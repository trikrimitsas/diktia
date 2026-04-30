"""Reputation score updates for Phase 2 (pure calculation)."""

BETA = 0.25
INITIAL = 1.0


def update_reputation(old: float, outcome: float) -> float:
    """
    new = (1 - beta) * old + beta * outcome
    PRD: new = 0.75 * old + 0.25 * outcome with beta = 0.25.
    outcome is 1.0 for successful transaction, 0.0 for cancel/failure.
    """
    return (1.0 - BETA) * float(old) + BETA * float(outcome)
