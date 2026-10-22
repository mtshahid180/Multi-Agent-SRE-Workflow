"""Tiny statistics helpers (contains an intentional bug for demos)."""


def average(values: list[float]) -> float:
    total = sum(values)
    return total / len(values)


def maximum(values: list[float]) -> float:
    return max(values)
