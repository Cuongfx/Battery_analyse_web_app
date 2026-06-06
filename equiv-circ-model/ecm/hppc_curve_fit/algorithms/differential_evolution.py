from scipy.optimize import differential_evolution

from .common import default_bounds, fit_setup, score_fit


def fit(t_curve, v_curve, rc_order, guess=None, bounds=None, maxfev=None):
    """
    Global optimization fit using differential evolution.
    """
    func, _ = fit_setup(rc_order)
    fit_bounds = default_bounds(v_curve, rc_order) if bounds is None else bounds
    maxiter = 100 if maxfev is None else max(1, int(maxfev))

    def objective(params):
        return score_fit(params, func, t_curve, v_curve)

    result = differential_evolution(
        objective,
        list(zip(fit_bounds[0], fit_bounds[1])),
        maxiter=maxiter,
        polish=True,
    )

    return result.x
