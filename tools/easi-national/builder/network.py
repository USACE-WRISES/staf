"""The NHDPlus V2 network from the VAA slim table, for the NRSA connectivity
walk the app performs through NLDI (``nrsa._connected_comids``): the main
path upstream (``uphydroseq`` chain) and downstream (``dnhydroseq`` chain)
within a distance along the network.
"""
from __future__ import annotations

import threading
from typing import Optional

from .paths import DataRoot

_CACHE: dict[str, "NetworkIndex"] = {}
_LOCK = threading.Lock()


class NetworkIndex:
    def __init__(self, comid, hydroseq, uphydroseq, dnhydroseq, lengthkm):
        import numpy as np
        order = np.argsort(hydroseq, kind="stable")
        self.hydroseq = hydroseq[order]
        self.comid = comid[order]
        self.up = uphydroseq[order]
        self.dn = dnhydroseq[order]
        self.length = lengthkm[order]
        by_comid = np.argsort(comid, kind="stable")
        self._comids_sorted = comid[by_comid]
        self._comid_pos = by_comid

    @classmethod
    def load(cls, root: DataRoot) -> "NetworkIndex":
        key = str(root.vaa)
        with _LOCK:
            hit = _CACHE.get(key)
            if hit is not None:
                return hit
        import numpy as np
        import pyarrow.parquet as pq
        table = pq.read_table(root.vaa, columns=["comid", "hydroseq", "uphydroseq",
                                                  "dnhydroseq", "lengthkm"])

        def col(name, dtype):
            values = table.column(name).to_pandas().fillna(0)
            return np.asarray(values, dtype=dtype)
        index = cls(col("comid", "int64"), col("hydroseq", "int64"), col("uphydroseq", "int64"),
                    col("dnhydroseq", "int64"), col("lengthkm", "float64"))
        with _LOCK:
            _CACHE[key] = index
        return index

    def _row_by_hydroseq(self, hydroseq: int) -> Optional[int]:
        import numpy as np
        pos = int(np.searchsorted(self.hydroseq, hydroseq))
        if pos < len(self.hydroseq) and self.hydroseq[pos] == hydroseq:
            return pos
        return None

    def row_by_comid(self, comid: int) -> Optional[int]:
        import numpy as np
        pos = int(np.searchsorted(self._comids_sorted, comid))
        if pos < len(self._comids_sorted) and self._comids_sorted[pos] == comid:
            return int(self._comid_pos[pos])
        return None

    def upstream_main(self, comid: int, distance_km: float) -> Optional[list[int]]:
        """The main path upstream of ``comid`` (the ``uphydroseq`` chain, what
        NLDI's upstreamMain navigation follows) within ``distance_km`` along
        the network, starting with the reach itself; None when the reach is
        not in the network. Never stops at a HUC8, state or region border."""
        start = self.row_by_comid(int(comid))
        if start is None:
            return None
        chain = [int(comid)]
        row, travelled = start, 0.0
        for _ in range(100_000):
            nxt = int(self.up[row])
            if nxt <= 0:
                break
            pos = self._row_by_hydroseq(nxt)
            if pos is None:
                break
            travelled += float(self.length[row])
            if travelled > distance_km:
                break
            chain.append(int(self.comid[pos]))
            row = pos
        return chain

    def connected_main(self, comid: int, distance_km: float) -> Optional[set[int]]:
        """COMIDs on the main path up- and downstream of ``comid`` within
        ``distance_km`` along the network (the start reach included), or None
        when the reach is not in the network."""
        start = self.row_by_comid(int(comid))
        if start is None:
            return None
        found = {int(comid)}
        for chain in (self.up, self.dn):
            row, travelled = start, 0.0
            for _ in range(100_000):
                nxt = int(chain[row])
                if nxt <= 0:
                    break
                pos = self._row_by_hydroseq(nxt)
                if pos is None:
                    break
                travelled += float(self.length[row])
                if travelled > distance_km:
                    break
                found.add(int(self.comid[pos]))
                row = pos
        return found


def connected_comids_factory(root: DataRoot):
    """A drop-in for ``easi.datasources.nrsa._connected_comids``."""
    index = NetworkIndex.load(root)

    def _connected(comid: int, distance_km: float):
        return index.connected_main(int(comid), float(distance_km))
    return _connected
