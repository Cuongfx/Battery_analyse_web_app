import numpy as np


def mean_absolute_error(actual, predicted):
    return float(np.mean(np.abs(np.asarray(actual) - np.asarray(predicted))))


def root_mean_square_error(actual, predicted):
    error = np.asarray(actual) - np.asarray(predicted)
    return float(np.sqrt(np.mean(error ** 2)))
