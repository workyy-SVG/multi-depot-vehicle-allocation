"""Deterministic vehicle moves and swaps using cached depot-period costs."""
from functools import lru_cache

from refine_eval import Depot, Scenario


class FleetEvaluator:
    def __init__(self, data):
        self.data = data
        self.depots = [Depot(xy - depot) for xy, depot in zip(data['coords'], data['depots'])]
        self.scenario = lru_cache(maxsize=None)(self.scenario)
        self.period_cost = lru_cache(maxsize=None)(self.period_cost)

    def scenario(self, depot, period):
        return Scenario(self.depots[depot], self.data['hist'][depot][:, period],
                        order_rot=3)

    def period_cost(self, depot, fleet, period):
        scenario = self.scenario(depot, period)
        return scenario.eval(fleet, rotations=6, refine_top=1, use_dp=False,
                             improve=False)

    def cost(self, depot, fleet, periods):
        return sum(self.period_cost(depot, fleet, t) for t in periods) / len(periods)

    def capacities(self, vehicles):
        return tuple(sorted(self.data['caps'][v] for v in vehicles))

    def score(self, allocation, periods):
        periods = tuple(periods)
        return sum(self.cost(d, self.capacities(ids), periods)
                   for d, ids in enumerate(allocation))


def neighbors(groups, capacities):
    for left, vehicles in enumerate(groups):
        representatives = {capacities[v]: v for v in vehicles}
        for right, others in enumerate(groups):
            if left == right:
                continue
            for vehicle in representatives.values():
                yield left, right, vehicle, None
            if left < right:
                for cap, vehicle in representatives.items():
                    for other_cap, other in {capacities[v]: v for v in others}.items():
                        if cap != other_cap:
                            yield left, right, vehicle, other


def improve_fleet(evaluator, candidates, periods, rounds):
    periods = tuple(periods)
    seed = min(candidates, key=lambda name: evaluator.score(candidates[name], periods))
    groups = [list(ids) for ids in candidates[seed]]
    for _ in range(rounds):
        costs = [evaluator.cost(d, evaluator.capacities(ids), periods)
                 for d, ids in enumerate(groups)]
        best_delta = -1e-8
        best = None
        for left, right, vehicle, other in neighbors(groups, evaluator.data['caps']):
            a, b = groups[left].copy(), groups[right].copy()
            a.remove(vehicle)
            b.append(vehicle)
            if other is not None:
                b.remove(other)
                a.append(other)
            delta = (evaluator.cost(left, evaluator.capacities(a), periods)
                     + evaluator.cost(right, evaluator.capacities(b), periods)
                     - costs[left] - costs[right])
            if delta < best_delta:
                best_delta = delta
                best = left, right, a, b
        if best is None:
            break
        left, right, groups_left, groups_right = best
        groups[left], groups[right] = groups_left, groups_right
    return seed, groups

