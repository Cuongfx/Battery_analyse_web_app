import numpy as np
import pandas as pd


class CellHppcData:
    """
    HPPC battery cell test data in the ORNL/Nissan Leaf CSV format.
    """

    def __init__(self, path, all_data=False):
        df = pd.read_csv(path)

        if all_data:
            self.time = df["Time(s)"].values
            self.current = df["Current(A)"].values
            self.voltage = df["Voltage(V)"].values
            self.flags = df["Data"].fillna(" ").values
            return

        time = df["Time(s)"].values
        current = df["Current(A)"].values
        voltage = df["Voltage(V)"].values
        flags = df["Data"].fillna(" ").values

        ids = np.where(flags == "S")[0]
        self.time = time[ids[1]:] - time[ids[1]]
        self.current = current[ids[1]:]
        self.voltage = voltage[ids[1]:]
        self.flags = flags[ids[1]:]

    def get_indices_s(self):
        return np.where(self.flags == "S")[0]

    def get_indices_q(self):
        return np.where(self.flags == "Q")[0]

    def get_indices_pulse(self):
        ids = self.get_indices_s()
        id0 = ids[0::5]
        id1 = id0 + 1
        id2 = ids[1::5]
        id3 = id2 + 1
        id4 = ids[2::5]
        return id0, id1, id2, id3, id4

    def get_indices_discharge(self):
        ids = self.get_indices_s()
        id0 = ids[3::5][:-1]
        id1 = id0 + 1
        id2 = ids[4::5]
        id3 = id2 + 1
        id4 = ids[5::5]
        return id0, id1, id2, id3, id4
