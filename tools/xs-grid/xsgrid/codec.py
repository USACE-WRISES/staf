"""Exact storage of a float32 transect as small integers.

Each float32 is mapped to an order-preserving 32-bit integer (consecutive floats get consecutive
integers, NaN included as a bit pattern), then coded as second differences in wrap-around
arithmetic, which is always exactly reversible. Neighbouring 1 m samples differ by a few thousand
float steps, so the integers are small and compress well under zstd: 1.84 bytes a sample on the 262
measured sections (float32 written as is: 2.73; float64: 5.34 at best).
"""
from __future__ import annotations

import numpy as np

_SIGN = np.uint32(0x80000000)
_ALL = np.uint32(0xFFFFFFFF)
ORDER = 2


def keys(z32: np.ndarray) -> np.ndarray:
    """float32 -> order-preserving uint32."""
    u = np.ascontiguousarray(z32, dtype=np.float32).view(np.uint32)
    return u ^ np.where(u >> 31, _ALL, _SIGN).astype(np.uint32)


def unkeys(k: np.ndarray) -> np.ndarray:
    k = np.ascontiguousarray(k, dtype=np.uint32)
    return (k ^ np.where(k >> 31, _SIGN, _ALL).astype(np.uint32)).view(np.float32)


def encode(z32: np.ndarray) -> np.ndarray:
    """``int32`` codes of a float32 transect (bit for bit reversible by :func:`decode`)."""
    d = keys(z32).copy()
    for _ in range(ORDER):
        d[1:] = d[1:] - d[:-1]                       # uint32 wrap-around
    return d.view(np.int32)


def decode(codes: np.ndarray) -> np.ndarray:
    k = np.ascontiguousarray(codes, dtype=np.int32).view(np.uint32)
    for _ in range(ORDER):
        k = np.cumsum(k, dtype=np.uint32)
    return unkeys(k)
