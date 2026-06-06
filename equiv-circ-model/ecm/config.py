from dataclasses import dataclass


@dataclass
class EcmConfig:
    """
    Configuration values used by the battery cell electrical ECM steps.
    """

    eta_chg: float = 0.98
    eta_dis: float = 1.00
    q_cell: float = 30.6

    @classmethod
    def from_object(cls, params):
        """
        Build a config from a params object. Missing values fall back to the
        defaults above.
        """
        defaults = cls()
        values = {
            field: getattr(params, field, getattr(defaults, field))
            for field in defaults.__dataclass_fields__
        }
        return cls(**values)

