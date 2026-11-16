"""Exact comparison of research reports that contain immutable numeric buffers."""
import numpy as np


def publication_bits(value):
    if isinstance(value, np.ndarray):
        return (value.shape, value.dtype.str, value.tobytes())
    if isinstance(value, dict):
        return {key: publication_bits(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [publication_bits(item) for item in value]
    return value
