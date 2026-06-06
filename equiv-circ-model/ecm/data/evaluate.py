import numpy as np
import pandas as pd


class CellDischargeData:
    """
    Battery cell data from a charge/discharge evaluation test.
    """

    def __init__(self, path):
        df = pd.read_csv(path)
        self.time = df["Time(s)"].values
        self.current = df["Current(A)"].values
        self.voltage = df["Voltage(V)"].values
        self.data = df["Data"].fillna(" ").values
        self.ti = 0
        self.tf = 0

    def get_ids(self):
        return np.where(self.data == "S")[0]

    def get_idx(self):
        ids = self.get_ids()

        if max(abs(self.current)) > 35:
            id0 = ids[3]
            id1 = ids[4]
            id2 = ids[5]
            id3 = ids[6]
        else:
            id0 = ids[2]
            id1 = ids[3]
            id2 = ids[4]
            id3 = ids[5]

        return id0, id1, id2, id3

    @classmethod
    def process(cls, path):
        data = cls(path)
        id0, _, id2, _ = data.get_idx()

        data.ti = data.time[id0]
        data.tf = data.time[id2]
        data.current = data.current[id0:id2 + 1]
        data.voltage = data.voltage[id0:id2 + 1]
        data.time = data.time[id0:id2 + 1] - data.time[id0:id2 + 1].min()
        return data

    @classmethod
    def process_discharge_only(cls, path):
        data = cls(path)
        id0, id1, _, _ = data.get_idx()

        data.ti = data.time[id0]
        data.tf = data.time[id1]
        data.current = data.current[id0:id1 + 1]
        data.voltage = data.voltage[id0:id1 + 1]
        data.time = data.time[id0:id1 + 1] - data.time[id0:id1 + 1][0]
        return data
