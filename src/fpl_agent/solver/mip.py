"""A tiny sparse MILP builder on top of scipy.optimize.milp (HiGHS).

Variables are registered under hashable keys, constraints are dicts of {key: coefficient}. The
objective is *maximised* (the builder negates it for scipy).
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array


class Infeasible(Exception):
    """The solver proved there is no solution (or found none within the time limit)."""


@dataclass
class Model:
    keys: list[Hashable] = field(default_factory=list)
    index: dict[Hashable, int] = field(default_factory=dict)
    lb: list[float] = field(default_factory=list)
    ub: list[float] = field(default_factory=list)
    integer: list[int] = field(default_factory=list)
    obj: list[float] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)
    cols: list[int] = field(default_factory=list)
    vals: list[float] = field(default_factory=list)
    lo: list[float] = field(default_factory=list)
    hi: list[float] = field(default_factory=list)

    def var(self, key: Hashable, lb: float = 0.0, ub: float = 1.0, integer: bool = True,
            obj: float = 0.0) -> int:  # fmt: skip
        if key in self.index:
            raise KeyError(f"duplicate variable {key!r}")
        i = len(self.keys)
        self.keys.append(key)
        self.index[key] = i
        self.lb.append(lb)
        self.ub.append(ub)
        self.integer.append(1 if integer else 0)
        self.obj.append(obj)
        return i

    def add_obj(self, key: Hashable, coef: float) -> None:
        self.obj[self.index[key]] += coef

    def con(self, coeffs: dict[Hashable, float], lo: float = -np.inf, hi: float = np.inf) -> None:
        r = len(self.lo)
        for k, v in coeffs.items():
            if v != 0.0:
                self.rows.append(r)
                self.cols.append(self.index[k])
                self.vals.append(float(v))
        self.lo.append(lo)
        self.hi.append(hi)

    def eq(self, coeffs: dict[Hashable, float], rhs: float) -> None:
        self.con(coeffs, rhs, rhs)

    def le(self, coeffs: dict[Hashable, float], rhs: float) -> None:
        self.con(coeffs, -np.inf, rhs)

    def ge(self, coeffs: dict[Hashable, float], rhs: float) -> None:
        self.con(coeffs, rhs, np.inf)

    def solve(
        self, time_limit: float = 60.0, gap: float = 1e-3
    ) -> tuple[dict[Hashable, float], float]:
        n, m = len(self.keys), len(self.lo)
        if m:
            a = coo_array((self.vals, (self.rows, self.cols)), shape=(m, n)).tocsr()
            cons = [LinearConstraint(a, np.array(self.lo), np.array(self.hi))]
        else:
            cons = []
        res = milp(
            c=-np.array(self.obj),
            integrality=np.array(self.integer),
            bounds=Bounds(np.array(self.lb), np.array(self.ub)),
            constraints=cons,
            options={"time_limit": time_limit, "mip_rel_gap": gap, "presolve": True},
        )
        if res.x is None:
            raise Infeasible(res.message)
        return {k: float(res.x[i]) for k, i in self.index.items()}, float(-res.fun)
