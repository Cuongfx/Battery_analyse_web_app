from dataclasses import dataclass

from ecm.config import EcmConfig
from ecm.equivalent_circuit.ocv import interpolate_ocv
from ecm.equivalent_circuit.simulate import get_rc_values, simulate_voltage
from ecm.equivalent_circuit.soc import calculate_soc
from ecm.hppc_curve_fit.fit import curve_fit_coefficients, func_otc, func_ttc
from ecm.hppc_curve_fit.rc_params import rctau_from_coefficients


@dataclass
class FitResult:
    soc: object
    ocv: object
    coefficients: object
    rctau: object
    vt: object
    ocv_points: tuple
    soc_points: object = None


class EquivalentCircuitModel:
    """
    Task-oriented ECM wrapper for HPPC fitting and voltage simulation.
    """

    func_otc = staticmethod(func_otc)
    func_ttc = staticmethod(func_ttc)
    get_rtau = staticmethod(get_rc_values)

    def __init__(
        self,
        data,
        params=None,
        *,
        capacity_attr="q_cell",
        ocv_anchor="pulse",
        trim_last_fit=False,
        include_fit_endpoint=False,
        fit_guess=None,
        fit_maxfev=None,
    ):
        self.current = data.current
        self.time = data.time
        self.voltage = data.voltage
        self.idd = data.get_indices_discharge()
        self.idp = data.get_indices_pulse() if hasattr(data, "get_indices_pulse") else None
        self.config = params if isinstance(params, EcmConfig) else EcmConfig.from_object(params) if params else EcmConfig()
        self.capacity_attr = capacity_attr
        self.ocv_anchor = ocv_anchor
        self.trim_last_fit = trim_last_fit
        self.include_fit_endpoint = include_fit_endpoint
        self.fit_guess = fit_guess
        self.fit_maxfev = fit_maxfev

    @property
    def capacity_ah(self):
        return getattr(self.config, self.capacity_attr)

    def soc(self):
        return calculate_soc(
            self.current,
            self.time,
            self.capacity_ah,
            eta_chg=self.config.eta_chg,
            eta_dis=self.config.eta_dis,
        )

    def _ocv_anchor_indices(self):
        if self.ocv_anchor == "pulse" and self.idp is not None:
            return self.idp[0]
        if self.ocv_anchor == "discharge":
            return self.idd[0]
        if self.idp is not None:
            return self.idp[0]
        return self.idd[0]

    def ocv(self, soc, pts=False, vz_pts=None):
        return interpolate_ocv(
            self.current,
            self.time,
            self.voltage,
            soc,
            anchor_indices=self._ocv_anchor_indices(),
            vz_points=vz_pts,
            pts=pts,
        )

    def _sections(self, source):
        if source == "pulse":
            if self.idp is None:
                raise ValueError("Pulse indices are not available for this data.")
            return self.idp
        if source == "discharge":
            return self.idd
        raise ValueError("source must be 'pulse' or 'discharge'")

    def curve_fit_coeff(self, rc_order=2, source="pulse"):
        sections = self._sections(source)
        # Trimming/endpoint handling only applies to the discharge sections.
        trim_last = self.trim_last_fit if source == "discharge" else False
        include_endpoint = self.include_fit_endpoint if source == "discharge" else False
        return curve_fit_coefficients(
            self.time,
            self.voltage,
            sections,
            rc_order=rc_order,
            trim_last=trim_last,
            include_endpoint=include_endpoint,
            algorithm=getattr(self, "fit_algorithm", "curve_fit"),
            guess=self.fit_guess,
            maxfev=self.fit_maxfev,
        )

    def rctau(self, coeff, rc_order=2, source="pulse"):
        if rc_order not in (1, 2):
            raise ValueError("rc_order must be 1 or 2")
        return rctau_from_coefficients(
            self.current,
            self.time,
            self.voltage,
            self._sections(source),
            coeff,
            rc_order=rc_order,
        )

    # Backwards-compatible helpers.
    def rctau_otc(self, coeff, source="discharge"):
        return self.rctau(coeff, rc_order=1, source=source)

    def rctau_ttc(self, coeff, source="discharge"):
        return self.rctau(coeff, rc_order=2, source=source)

    def vt(self, soc, ocv, rctau, soc_points=None, rc_order=None):
        return simulate_voltage(
            self.time, self.current, soc, ocv, rctau, soc_points=soc_points, rc_order=rc_order
        )

    def fit(self, rc_order=2, algorithm="curve_fit", source="pulse"):
        self.fit_algorithm = algorithm
        soc = self.soc()
        ocv, _, _, v_pts, z_pts = self.ocv(soc, pts=True)
        coeff = self.curve_fit_coeff(rc_order=rc_order, source=source)
        rctau = self.rctau(coeff, rc_order=rc_order, source=source)

        # SOC associated with each parameter row (rested point before each step).
        section_start = self._sections(source)[0][:len(rctau)]
        soc_points = soc[section_start]

        vt = self.vt(soc, ocv, rctau, soc_points=soc_points, rc_order=rc_order)
        return FitResult(
            soc=soc,
            ocv=ocv,
            coefficients=coeff,
            rctau=rctau,
            vt=vt,
            ocv_points=(v_pts, z_pts),
            soc_points=soc_points,
        )


class CellEcm(EquivalentCircuitModel):
    def __init__(self, data, params=None):
        super().__init__(
            data,
            params,
            capacity_attr="q_cell",
            ocv_anchor="pulse",
            trim_last_fit=False,
        )


def fit_ecm_from_hppc(data, params=None, rc_order=2, algorithm="curve_fit", source="pulse"):
    """
    Fit a battery cell ECM from HPPC data with a selectable RC order.

    `source` selects whether RC parameters come from the short HPPC pulses
    ("pulse", one row per SOC level including 100%) or the constant-discharge
    relaxations ("discharge").
    """
    model = CellEcm(data, params)
    return model, model.fit(rc_order=rc_order, algorithm=algorithm, source=source)
