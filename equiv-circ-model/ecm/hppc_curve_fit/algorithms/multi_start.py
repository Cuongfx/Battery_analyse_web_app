from scipy.optimize import curve_fit

from .common import clip_guess_to_bounds, default_bounds, fit_setup, multistart_guesses, score_fit


def fit(t_curve, v_curve, rc_order, guess=None, bounds=None, maxfev=None):
    """
    Run curve_fit from many initial guesses and keep the lowest-RMSE result.
    """
    func, _ = fit_setup(rc_order)
    fit_bounds = default_bounds(v_curve, rc_order) if bounds is None else bounds
    guesses = [guess] if guess is not None else multistart_guesses(v_curve, rc_order)
    best_params = None
    best_score = float("inf")

    for p0 in guesses:
        kwargs = {
            "p0": clip_guess_to_bounds(p0, fit_bounds),
            "bounds": fit_bounds,
        }
        if maxfev is not None:
            kwargs["maxfev"] = maxfev

        try:
            params, _ = curve_fit(func, t_curve, v_curve, **kwargs)
        except (RuntimeError, ValueError):
            continue

        score = score_fit(params, func, t_curve, v_curve)
        if score < best_score:
            best_params = params
            best_score = score

    if best_params is None:
        raise RuntimeError("multi_start curve fitting failed for all initial guesses")

    return best_params
