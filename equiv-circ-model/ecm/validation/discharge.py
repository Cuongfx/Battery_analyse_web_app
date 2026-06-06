from dataclasses import dataclass

from ecm.equivalent_circuit.ocv import interpolate_ocv
from ecm.equivalent_circuit.simulate import simulate_voltage
from ecm.equivalent_circuit.soc import calculate_soc
from ecm.validation.metrics import mean_absolute_error, root_mean_square_error


@dataclass
class EvaluationResult:
    soc: object
    ocv: object
    vt: object
    mae: float
    rmse: float


def simulate_evaluation_profile(data, config, fit_result, *, rc_order=2, capacity_attr="q_cell"):
    """
    Simulate a non-HPPC evaluation profile using OCV points and RC parameters
    identified from HPPC data.
    """
    capacity_ah = getattr(config, capacity_attr)
    soc = calculate_soc(
        data.current,
        data.time,
        capacity_ah,
        eta_chg=config.eta_chg,
        eta_dis=config.eta_dis,
    )
    ocv = interpolate_ocv(
        data.current,
        data.time,
        data.voltage,
        soc,
        vz_points=fit_result.ocv_points,
    )
    vt = simulate_voltage(
        data.time,
        data.current,
        soc,
        ocv,
        fit_result.rctau,
        soc_points=fit_result.soc_points,
        rc_order=rc_order,
    )

    return EvaluationResult(
        soc=soc,
        ocv=ocv,
        vt=vt,
        mae=mean_absolute_error(data.voltage, vt),
        rmse=root_mean_square_error(data.voltage, vt),
    )
