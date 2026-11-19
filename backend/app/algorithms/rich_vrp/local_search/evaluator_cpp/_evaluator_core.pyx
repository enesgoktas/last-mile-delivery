# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: nonecheck=False
"""
Cython hot-path evaluator for the Rich VRP engine.

Exports
-------
forward_pass_c
    Compute forward arrival times for a flattened route.
backward_pass_c
    Compute backward latest-arrival times for a flattened route.
check_pd_insertion_c
    O(1) feasibility check for adjacent PD-pair insertion; O(de-pu) for
    non-adjacent, all in C arithmetic — no Python object overhead.
"""

import numpy as np
cimport numpy as cnp
from numpy cimport int64_t

cnp.import_array()

DTYPE = np.int64


def forward_pass_c(
    cnp.ndarray[int64_t, ndim=2] dur_matrix not None,
    cnp.ndarray[int64_t, ndim=1] nodes not None,
    cnp.ndarray[int64_t, ndim=1] tw_open not None,
    cnp.ndarray[int64_t, ndim=1] service_s not None,
    long long shift_start,
):
    """Compute forward arrival times for every visit in a route.

    Parameters
    ----------
    dur_matrix : int64[M, M]
        Full duration matrix (seconds).
    nodes : int64[n]
        Matrix node index per visit (nodes[0] = DEPOT_START node).
    tw_open : int64[n]
        Earliest start of service per visit (0 = no window / DEPOT).
    service_s : int64[n]
        Service duration per visit in seconds (0 for DEPOT_START/END).
    shift_start : int
        Vehicle shift start time in seconds.

    Returns
    -------
    arrivals : int64[n]
        Arrival time at each visit (capped up to tw_open if waiting).
    """
    cdef int n = nodes.shape[0]
    cdef cnp.ndarray[int64_t, ndim=1] arrivals = np.empty(n, dtype=DTYPE)
    cdef long long prev_dep
    cdef long long prev_node
    cdef long long travel, arrival, tw
    cdef int i

    if n == 0:
        return arrivals

    # Visit 0 is always DEPOT_START
    arrivals[0] = shift_start
    prev_dep = shift_start
    prev_node = nodes[0]

    for i in range(1, n):
        travel = dur_matrix[prev_node, nodes[i]]
        arrival = prev_dep + travel
        tw = tw_open[i]
        if tw > 0 and arrival < tw:
            arrival = tw
        arrivals[i] = arrival
        prev_dep = arrival + service_s[i]
        prev_node = nodes[i]

    return arrivals


def backward_pass_c(
    cnp.ndarray[int64_t, ndim=2] dur_matrix not None,
    cnp.ndarray[int64_t, ndim=1] nodes not None,
    cnp.ndarray[int64_t, ndim=1] tw_close not None,
    cnp.ndarray[int64_t, ndim=1] service_s not None,
    long long shift_hard_end,
):
    """Compute backward latest-arrival times for every visit in a route.

    latest[i] = latest arrival time at visit i such that all downstream
    time windows are still reachable.

    Parameters
    ----------
    dur_matrix : int64[M, M]
    nodes : int64[n]
    tw_close : int64[n]
        Latest finish of service per visit (0 = DEPOT_END, use shift_hard_end).
    service_s : int64[n]
        Service duration per visit in seconds.
    shift_hard_end : int
        Hard shift end time for DEPOT_END.

    Returns
    -------
    latest : int64[n]
    """
    cdef int n = nodes.shape[0]
    cdef cnp.ndarray[int64_t, ndim=1] latest = np.empty(n, dtype=DTYPE)
    cdef long long travel, tw_c, lat
    cdef int i

    if n == 0:
        return latest

    latest[n - 1] = shift_hard_end

    for i in range(n - 2, -1, -1):
        travel = dur_matrix[nodes[i], nodes[i + 1]]
        # latest arrival at i: arrival_i + service_i + travel <= latest[i+1]
        # => arrival_i <= latest[i+1] - travel - service_i
        lat = latest[i + 1] - travel - service_s[i]
        tw_c = tw_close[i]
        if tw_c > 0 and lat > tw_c:
            lat = tw_c
        latest[i] = lat

    return latest


def check_pd_insertion_c(
    cnp.ndarray[int64_t, ndim=1] forward_arrival not None,
    cnp.ndarray[int64_t, ndim=1] service_arr not None,
    cnp.ndarray[int64_t, ndim=1] backward_latest not None,
    cnp.ndarray[int64_t, ndim=1] nodes not None,
    cnp.ndarray[int64_t, ndim=1] tw_open_arr not None,
    cnp.ndarray[int64_t, ndim=2] dur_matrix not None,
    int pu,
    int de,
    long long pu_node,
    long long de_node,
    long long pu_tw_open,
    long long de_tw_open,
    long long de_hard_deadline,
    long long pu_service,
    long long de_service,
    long long capacity_slack,
    long long request_load,
    long long shift_hard_end,
):
    """Check feasibility of inserting a PD-pair into a route.

    Uses precomputed forward_arrival and backward_latest arrays to avoid
    full O(n) re-evaluation.

    Parameters
    ----------
    forward_arrival : int64[n]
        Arrival times at each visit in the current (unmodified) route.
    service_arr : int64[n]
        Service times at each visit.
    backward_latest : int64[n]
        Latest arrival times at each visit.
    nodes : int64[n]
        Node indices of existing visits.
    tw_open_arr : int64[n]
        TW open per existing visit (0 = no window).
    dur_matrix : int64[M, M]
    pu : int
        Insert-before position for pickup (1 ≤ pu < n).
    de : int
        Insert-before position for delivery in the shifted list (pu+1 ≤ de ≤ n).
    pu_node, de_node : matrix node indices for pickup/delivery.
    pu_tw_open, de_tw_open : TW opens (0 = no window).
    de_hard_deadline : hard deadline for delivery (-1 = none).
    pu_service, de_service : service times in seconds.
    capacity_slack : remaining capacity (primary dimension, e.g. weight_g).
    request_load : request load (primary dimension).
    shift_hard_end : vehicle hard shift end.

    Returns
    -------
    int : 1 if feasible, 0 if infeasible.
    """
    cdef int n = nodes.shape[0]
    cdef int k, next_idx
    cdef long long arrive_pu, depart_pu, arrive_de, depart_de
    cdef long long prev_dep, prev_node_inner, travel, arrival, dep, tw
    cdef long long next_node, lat

    # Capacity check (single dimension — caller has already aggregated slack)
    if capacity_slack < request_load:
        return 0

    # Departure from the visit just before pickup (index pu-1)
    if pu < 1 or pu >= n:
        return 0
    prev_dep = forward_arrival[pu - 1] + service_arr[pu - 1]

    # Arrival at pickup
    travel = dur_matrix[nodes[pu - 1], pu_node]
    arrive_pu = prev_dep + travel
    if pu_tw_open > 0 and arrive_pu < pu_tw_open:
        arrive_pu = pu_tw_open
    depart_pu = arrive_pu + pu_service

    if de == pu + 1:
        # --- Adjacent case: O(1) ---
        # Delivery immediately follows pickup in the new list.
        travel = dur_matrix[pu_node, de_node]
        arrive_de = depart_pu + travel
        if de_tw_open > 0 and arrive_de < de_tw_open:
            arrive_de = de_tw_open
        if de_hard_deadline >= 0 and arrive_de > de_hard_deadline:
            return 0
        depart_de = arrive_de + de_service

        # Next original visit after delivery is nodes[pu] (de-1 = pu in original).
        # Check that it can still be reached in time.
        if pu < n:
            next_node = nodes[pu]
            travel = dur_matrix[de_node, next_node]
            lat = backward_latest[pu]
            if depart_de + travel > lat:
                return 0
        else:
            # Delivery is inserted before DEPOT_END
            travel = dur_matrix[de_node, nodes[n - 1]]
            if depart_de + travel > shift_hard_end:
                return 0
    else:
        # --- Non-adjacent case: O(de - pu) forward propagation in C ---
        # Visits between pickup and delivery in the new route:
        # original visits [pu, pu+1, ..., de-2]
        prev_dep = depart_pu
        prev_node_inner = pu_node

        for k in range(pu, de - 1):
            if k >= n:
                break
            travel = dur_matrix[prev_node_inner, nodes[k]]
            arrival = prev_dep + travel
            tw = tw_open_arr[k]
            if tw > 0 and arrival < tw:
                arrival = tw
            # Cascading-delay guard: if this intermediate visit is pushed past
            # its backward-latest bound, all downstream constraints are violated.
            if arrival > backward_latest[k]:
                return 0
            dep = arrival + service_arr[k]
            prev_dep = dep
            prev_node_inner = nodes[k]

        # Arrival at delivery
        travel = dur_matrix[prev_node_inner, de_node]
        arrive_de = prev_dep + travel
        if de_tw_open > 0 and arrive_de < de_tw_open:
            arrive_de = de_tw_open
        if de_hard_deadline >= 0 and arrive_de > de_hard_deadline:
            return 0
        depart_de = arrive_de + de_service

        # Next original visit after delivery is nodes[de-1].
        next_idx = de - 1
        if next_idx < n:
            travel = dur_matrix[de_node, nodes[next_idx]]
            lat = backward_latest[next_idx]
            if depart_de + travel > lat:
                return 0
        else:
            # Delivery is at the very end (before DEPOT_END)
            travel = dur_matrix[de_node, nodes[n - 1]]
            if depart_de + travel > shift_hard_end:
                return 0

    return 1
