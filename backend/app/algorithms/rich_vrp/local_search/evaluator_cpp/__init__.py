"""Cython-compiled evaluator hot path for the Rich VRP engine.

Attempts to import the compiled C extension.  Falls back to pure-Python
equivalents if the extension has not been built, so the package always
works — just slower.

Attributes
----------
COMPILED : bool
    True when the Cython extension is loaded; False when running on the
    pure-Python fallback path.
forward_pass_c, backward_pass_c, check_pd_insertion_c
    Callable functions (either from the .so or the Python fallback).
"""
from __future__ import annotations

import numpy as np

try:
    from ._evaluator_core import (
        backward_pass_c,
        check_pd_insertion_c,
        forward_pass_c,
    )
    COMPILED = True
except ImportError:
    COMPILED = False

    # ------------------------------------------------------------------
    # Pure-Python fallbacks (same signatures, same semantics)
    # ------------------------------------------------------------------

    def forward_pass_c(dur_matrix, nodes, tw_open, service_s, shift_start):
        """Pure-Python fallback for forward_pass_c."""
        n = len(nodes)
        arrivals = np.empty(n, dtype=np.int64)
        if n == 0:
            return arrivals
        arrivals[0] = shift_start
        prev_dep = int(shift_start)
        prev_node = int(nodes[0])
        for i in range(1, n):
            travel = int(dur_matrix[prev_node, nodes[i]])
            arrival = prev_dep + travel
            tw = int(tw_open[i])
            if tw > 0 and arrival < tw:
                arrival = tw
            arrivals[i] = arrival
            prev_dep = arrival + int(service_s[i])
            prev_node = int(nodes[i])
        return arrivals

    def backward_pass_c(dur_matrix, nodes, tw_close, service_s, shift_hard_end):
        """Pure-Python fallback for backward_pass_c."""
        n = len(nodes)
        latest = np.empty(n, dtype=np.int64)
        if n == 0:
            return latest
        latest[n - 1] = int(shift_hard_end)
        for i in range(n - 2, -1, -1):
            travel = int(dur_matrix[nodes[i], nodes[i + 1]])
            lat = int(latest[i + 1]) - travel - int(service_s[i])
            tw_c = int(tw_close[i])
            if tw_c > 0 and lat > tw_c:
                lat = tw_c
            latest[i] = lat
        return latest

    def check_pd_insertion_c(
        forward_arrival, service_arr, backward_latest, nodes, tw_open_arr,
        dur_matrix, pu, de, pu_node, de_node, pu_tw_open, de_tw_open,
        de_hard_deadline, pu_service, de_service, capacity_slack, request_load,
        shift_hard_end,
    ):
        """Pure-Python fallback for check_pd_insertion_c."""
        n = len(nodes)
        if capacity_slack < request_load:
            return 0
        if pu < 1 or pu >= n:
            return 0

        prev_dep = int(forward_arrival[pu - 1]) + int(service_arr[pu - 1])
        travel = int(dur_matrix[int(nodes[pu - 1]), pu_node])
        arrive_pu = prev_dep + travel
        if pu_tw_open > 0 and arrive_pu < pu_tw_open:
            arrive_pu = pu_tw_open
        depart_pu = arrive_pu + pu_service

        if de == pu + 1:
            travel = int(dur_matrix[pu_node, de_node])
            arrive_de = depart_pu + travel
            if de_tw_open > 0 and arrive_de < de_tw_open:
                arrive_de = de_tw_open
            if de_hard_deadline >= 0 and arrive_de > de_hard_deadline:
                return 0
            depart_de = arrive_de + de_service
            if pu < n:
                travel = int(dur_matrix[de_node, int(nodes[pu])])
                if depart_de + travel > int(backward_latest[pu]):
                    return 0
            else:
                travel = int(dur_matrix[de_node, int(nodes[n - 1])])
                if depart_de + travel > shift_hard_end:
                    return 0
        else:
            prev_dep = depart_pu
            prev_node_inner = pu_node
            for k in range(pu, de - 1):
                if k >= n:
                    break
                travel = int(dur_matrix[prev_node_inner, int(nodes[k])])
                arrival = prev_dep + travel
                tw = int(tw_open_arr[k])
                if tw > 0 and arrival < tw:
                    arrival = tw
                # If this intermediate visit is pushed past its latest-arrival
                # bound, all downstream constraints are violated → infeasible.
                if arrival > int(backward_latest[k]):
                    return 0
                dep = arrival + int(service_arr[k])
                prev_dep = dep
                prev_node_inner = int(nodes[k])
            travel = int(dur_matrix[prev_node_inner, de_node])
            arrive_de = prev_dep + travel
            if de_tw_open > 0 and arrive_de < de_tw_open:
                arrive_de = de_tw_open
            if de_hard_deadline >= 0 and arrive_de > de_hard_deadline:
                return 0
            depart_de = arrive_de + de_service
            next_idx = de - 1
            if next_idx < n:
                travel = int(dur_matrix[de_node, int(nodes[next_idx])])
                if depart_de + travel > int(backward_latest[next_idx]):
                    return 0
            else:
                travel = int(dur_matrix[de_node, int(nodes[n - 1])])
                if depart_de + travel > shift_hard_end:
                    return 0

        return 1
