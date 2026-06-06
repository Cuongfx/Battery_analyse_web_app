from . import bounded_least_squares
from . import curve_fit
from . import differential_evolution
from . import multi_start
from . import robust_least_squares
from .common import func_otc, func_ttc

ALGORITHMS = {
    "curve_fit": curve_fit.fit,
    "multi_start": multi_start.fit,
    "bounded_ls": bounded_least_squares.fit,
    "robust_ls": robust_least_squares.fit,
    "differential_evolution": differential_evolution.fit,
}


def available_algorithms():
    return tuple(ALGORITHMS.keys())


def get_algorithm(name):
    try:
        return ALGORITHMS[name]
    except KeyError as exc:
        valid = ", ".join(available_algorithms())
        raise ValueError(f"Unknown fit algorithm '{name}'. Choose one of: {valid}") from exc


__all__ = [
    "ALGORITHMS",
    "available_algorithms",
    "func_otc",
    "func_ttc",
    "get_algorithm",
]
