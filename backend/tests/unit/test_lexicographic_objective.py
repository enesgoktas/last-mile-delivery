"""Tests for the lexicographic objective (M1.3)."""
from __future__ import annotations

from app.algorithms.rich_vrp.objective.lexicographic import (
    LexicographicObjective,
    unserved_weight,
)
from app.models.enums import OrderPriority


def _obj(unserved=0, late=0, cost=0, stability=0):
    return LexicographicObjective(
        unserved_count_weighted=unserved,
        lateness_and_overtime_min=late,
        generalized_cost=cost,
        stability_distance=stability,
    )


# --- Priority weighting ---

def test_urgent_weight_much_larger_than_low():
    assert unserved_weight(OrderPriority.URGENT) > unserved_weight(OrderPriority.HIGH)
    assert unserved_weight(OrderPriority.HIGH) > unserved_weight(OrderPriority.NORMAL)
    assert unserved_weight(OrderPriority.NORMAL) > unserved_weight(OrderPriority.LOW)


def test_one_urgent_worse_than_many_lows():
    urgent_unserved = _obj(unserved=unserved_weight(OrderPriority.URGENT))
    many_lows = _obj(unserved=unserved_weight(OrderPriority.LOW) * 100)
    # Dropping 1 URGENT is worse than dropping 100 LOW orders
    assert urgent_unserved > many_lows


# --- Lexicographic ordering ---

def test_unserved_dominates_all_else():
    worse_unserved = _obj(unserved=1, late=0, cost=0)
    better_unserved_high_cost = _obj(unserved=0, late=10000, cost=10_000_000)
    assert worse_unserved > better_unserved_high_cost


def test_zero_unserved_lateness_dominates_cost():
    high_late = _obj(unserved=0, late=1000, cost=0)
    low_late_high_cost = _obj(unserved=0, late=0, cost=1_000_000_000)
    assert high_late > low_late_high_cost


def test_equality():
    a = _obj(unserved=2, late=5, cost=100, stability=0)
    b = _obj(unserved=2, late=5, cost=100, stability=0)
    assert a == b
    assert not (a < b)
    assert not (a > b)
    assert a <= b
    assert a >= b


def test_lt_and_gt_consistent():
    small = _obj(cost=10)
    big = _obj(cost=20)
    assert small < big
    assert big > small
    assert not (small > big)


def test_add():
    a = _obj(unserved=1, late=2, cost=3, stability=4)
    b = _obj(unserved=10, late=20, cost=30, stability=40)
    c = a + b
    assert c.unserved_count_weighted == 11
    assert c.lateness_and_overtime_min == 22
    assert c.generalized_cost == 33
    assert c.stability_distance == 44


def test_as_tuple():
    obj = _obj(unserved=1, late=2, cost=3, stability=4)
    assert obj.as_tuple() == (1, 2, 3, 4)


# --- dominates ---

def test_dominates_strictly_better():
    a = _obj(cost=10)
    b = _obj(cost=20)
    assert a.dominates(b)
    assert not b.dominates(a)


def test_dominates_equal_is_false():
    a = _obj(cost=10)
    assert not a.dominates(a)


def test_dominates_better_on_later_component_only():
    a = _obj(unserved=0, late=0, cost=10)
    b = _obj(unserved=0, late=0, cost=20)
    assert a.dominates(b)


def test_dominates_worse_on_first_component():
    a = _obj(unserved=1, late=0, cost=0)
    b = _obj(unserved=0, late=1000, cost=1000)
    # b is better on component 0 → a does not dominate b
    assert not a.dominates(b)
