"""Deterministic expected-cost evaluator for fleet allocations.

For a fixed depot, customer coordinates are constant across demand periods, so a
good cyclic TSP order and an angular order over all customers are built once.
For each demand scenario the zero-demand customers are removed, the order is
split into capacity-feasible routes (greedy fill or exact split DP, a suffix may
be dropped at penalty 4*d(depot,customer)), and every route is improved with
2-opt.  Several rotations of both base orders are tried; the best cost is kept.

The evaluator is deterministic and much faster than repeated time-limited
OR-Tools calls, which makes it suitable for comparing allocations over every
historical period rather than a handful of noisy validation runs.
"""
import numpy as np

UNSERVED_DISTANCE_MULTIPLIER = 4


def cdist(X, Y):
    X = np.asarray(X, dtype=float)
    Y = np.asarray(Y, dtype=float)
    return np.sqrt(np.maximum(((X[:, None, :] - Y[None, :, :]) ** 2).sum(axis=2), 0.0))


def route_length(route, pts):
    if not route:
        return 0.0
    r = np.asarray([0] + list(route) + [0])
    return float(np.linalg.norm(np.diff(pts[r], axis=0), axis=1).sum())


def two_opt(route, pts, max_moves=None):
    """Best-improvement 2-opt with a vectorised delta matrix."""
    n = len(route)
    if n < 3:
        return list(route)
    r = [0] + list(route) + [0]
    P = pts[r]
    limit = max_moves if max_moves is not None else min(3 * n, 80)
    lower = np.tril_indices(n + 1)
    for _ in range(limit):
        A = P[:-1]
        B = P[1:]
        dAB = cdist(A, B)
        diag = dAB.diagonal()
        delta = cdist(A, A) + cdist(B, B) - diag[:, None] - diag[None, :]
        delta[lower] = 1e9
        np.fill_diagonal(delta, 1e9)
        k = int(np.argmin(delta))
        i, j = divmod(k, n + 1)
        if delta[i, j] >= -1e-9:
            break
        r[i + 1:j + 1] = r[i + 1:j + 1][::-1]
        P = pts[r]
    return r[1:-1]


def two_opt_cand(route, pts, nbrs, max_moves=200):
    route = list(route)
    n = len(route)
    if n < 4:
        return route
    pos = np.full(len(pts), -1, dtype=int)
    pos[0] = 0
    for k, v in enumerate(route):
        pos[v] = k + 1
    arr = [0] + route + [0]
    for _ in range(max_moves):
        improved = False
        for i in range(1, n):
            u = arr[i]
            v = arr[i + 1]
            for w in nbrs[u]:
                if w == 0 or w == u or w == v:
                    continue
                j = pos[w]
                if j <= i + 1 or j > n:
                    continue
                x, y = arr[j], arr[j + 1]
                delta = (np.linalg.norm(pts[u] - pts[w]) + np.linalg.norm(pts[v] - pts[y])
                         - np.linalg.norm(pts[u] - pts[v]) - np.linalg.norm(pts[w] - pts[y]))
                if delta < -1e-9:
                    arr[i + 1:j + 1] = arr[i + 1:j + 1][::-1]
                    for k in range(i + 1, j + 1):
                        pos[arr[k]] = k
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    return arr[1:-1]


def opt_route(route, pts, nbrs=None):
    if len(route) < 3:
        return list(route)
    if nbrs is not None and len(route) > 120:
        return two_opt_cand(route, pts, nbrs)
    return two_opt(route, pts)


def build_tour_all(xy):
    n = len(xy)
    if n == 0:
        return []
    visited = np.zeros(n, dtype=bool)
    cur = int(np.argmin(np.linalg.norm(xy, axis=1)))
    order = []
    for _ in range(n):
        visited[cur] = True
        order.append(cur)
        dist = np.linalg.norm(xy - xy[cur], axis=1)
        dist[visited] = np.inf
        cur = int(np.argmin(dist))
    tour = [i + 1 for i in order]
    return two_opt(tour, np.vstack([[0.0, 0.0], xy]), max_moves=12 if n > 400 else None)


def angular_all(xy):
    ang = np.arctan2(xy[:, 1], xy[:, 0])
    return [int(i) + 1 for i in np.argsort(ang)]


class Depot:
    def __init__(self, xy):
        self.xy = np.asarray(xy, dtype=float)
        self.pts = np.vstack([[0.0, 0.0], self.xy])
        self.penal_all = UNSERVED_DISTANCE_MULTIPLIER * np.linalg.norm(self.pts, axis=1)
        self.tour_all = build_tour_all(self.xy)
        self.ang_all = angular_all(self.xy)
        n = len(self.xy)
        if n > 0:
            dmat = cdist(self.pts, self.pts)
            K = min(12, n)
            self.nbrs = np.argsort(dmat, axis=1)[:, 1:K + 1]
        else:
            self.nbrs = None


class Scenario:
    def __init__(self, depot, q, order_rot=10, cut_rot=0):
        self.depot = depot
        self.q = np.asarray(q, dtype=float)
        self.qn = np.r_[0.0, self.q]
        self.active = np.flatnonzero(self.q > 0) + 1
        aset = set(int(v) for v in self.active)
        self.tour = [v for v in depot.tour_all if v in aset]
        self.ang = [v for v in depot.ang_all if v in aset]
        self.penal = depot.penal_all.copy()
        self.pts = depot.pts
        self.nbrs = depot.nbrs
        self.orders = []
        self._make_orders(order_rot, cut_rot)

    def _make_orders(self, order_rot, cut_rot):
        per_base = []
        for base in (self.tour, self.ang):
            n = len(base)
            if n == 0:
                per_base.append([])
                continue
            cuts = set()
            R = min(order_rot, n)
            for r in range(R):
                cuts.add((r * n) // R)
            if cut_rot:
                ins = []
                for i in range(n):
                    a = base[i]
                    b = base[(i + 1) % n]
                    s = (np.linalg.norm(self.pts[a]) + np.linalg.norm(self.pts[b])
                         - np.linalg.norm(self.pts[a] - self.pts[b]))
                    ins.append((s, (i + 1) % n))
                ins.sort()
                for _, c in ins[:cut_rot]:
                    cuts.add(c)
            orders = []
            for c in sorted(cuts):
                cyc = base[c:] + base[:c]
                orders.append(cyc)
                orders.append(list(reversed(cyc)))
            per_base.append(orders)
        self.orders = []
        for i in range(max(len(x) for x in per_base)):
            for orders in per_base:
                if i < len(orders):
                    self.orders.append(orders[i])

    def greedy(self, order, caps):
        cs = sorted([int(c) for c in caps if c > 0], reverse=True)
        routes = []
        dropped = []
        i = 0
        n = len(order)
        for cap in cs:
            route = []
            load = 0.0
            while i < n:
                j = order[i]
                qj = self.qn[j]
                if load + qj <= cap + 1e-9:
                    route.append(j)
                    load += qj
                    i += 1
                elif qj > cap + 1e-9:
                    dropped.append(j)
                    i += 1
                else:
                    break
            if route:
                routes.append(route)
        return routes, dropped + order[i:]

    def split_dp(self, order, caps):
        cs = sorted([int(c) for c in caps if c > 0], reverse=True)
        n = len(order)
        if n == 0:
            return [], []
        seq = [0] + list(order) + [0]
        edge = np.linalg.norm(np.diff(self.pts[seq], axis=0), axis=1)
        P = np.r_[0.0, np.cumsum(edge)]
        d0 = np.linalg.norm(self.pts[order], axis=1)
        INF = float('inf')
        m = len(cs)
        dp = np.full((m + 1, n + 1), INF)
        prev = np.zeros((m + 1, n + 1), dtype=int)
        dp[0, 0] = 0.0
        for k in range(1, m + 1):
            cap = cs[k - 1]
            dp[k] = dp[k - 1]
            prev[k] = np.arange(n + 1)
            for i in range(n):
                base = dp[k - 1, i]
                if base >= INF:
                    continue
                load = 0.0
                for j in range(i, n):
                    load += self.qn[order[j]]
                    if load > cap + 1e-9:
                        break
                    c = base + d0[i] + (P[j + 1] - P[i + 1]) + d0[j]
                    if c < dp[k, j + 1] - 1e-9:
                        dp[k, j + 1] = c
                        prev[k, j + 1] = i
        suffix_pen = np.r_[np.cumsum(self.penal[order][::-1])[::-1], 0.0]
        best = INF
        bi = bk = -1
        for k in range(1, m + 1):
            row = dp[k] + suffix_pen
            i = int(np.argmin(row))
            if row[i] < best:
                best = float(row[i])
                bi, bk = i, k
        if bi < 0:
            return [], order
        routes = []
        i, k = bi, bk
        while k > 0 and i > 0:
            p = int(prev[k, i])
            if p < i:
                routes.append(list(order[p:i]))
            i, k = p, k - 1
        routes.reverse()
        return routes, list(order[bi:])

    def raw_cost(self, routes, dropped):
        total = float(self.penal[dropped].sum()) if len(dropped) else 0.0
        for route in routes:
            total += route_length(route, self.pts)
        return total

    def cost_routes(self, routes, dropped, improve=True):
        total = float(self.penal[dropped].sum()) if len(dropped) else 0.0
        for route in routes:
            total += route_length(opt_route(route, self.pts, self.nbrs) if improve else route, self.pts)
        return total

    def eval(self, caps, rotations=None, dp_top=3, refine_top=6, use_dp=True, improve=True):
        caps = [int(c) for c in caps if c > 0]
        if len(self.active) == 0:
            return 0.0
        if not caps:
            return float(self.penal[self.active].sum())
        total_q = float(self.qn[self.active].sum())
        if max(caps) >= total_q and float(self.qn[self.active].max()) <= max(caps):
            best = float('inf')
            for base in (self.tour, self.ang):
                if not base:
                    continue
                best = min(best, route_length(opt_route(base, self.pts, self.nbrs) if improve else base, self.pts))
            return best
        orders = self.orders if rotations is None else self.orders[:rotations]
        scored = []
        for order in orders:
            routes, dropped = self.greedy(order, caps)
            scored.append((self.raw_cost(routes, dropped), order))
        scored.sort(key=lambda z: z[0])
        best = float('inf')
        for _, order in scored[:refine_top]:
            routes, dropped = self.greedy(order, caps)
            best = min(best, self.cost_routes(routes, dropped, improve))
        if use_dp:
            for _, order in scored[:dp_top]:
                routes, dropped = self.split_dp(order, caps)
                best = min(best, self.cost_routes(routes, dropped, improve))
        return best
