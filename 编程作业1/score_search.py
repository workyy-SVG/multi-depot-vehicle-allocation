"""Fleet search for getScore.py's mean-demand routing objective."""

from collections import Counter, defaultdict
from functools import lru_cache
from itertools import product
from math import prod
from random import Random
from time import perf_counter

import numpy as np
from pyvrp import Client, Depot, ProblemData, VehicleType, solve as route_solve
from pyvrp.stop import MaxRuntime

from fleet_search import neighbors


DISTANCE_SCALE = 1000
PENALTY_MULTIPLIER = 2


class ScoreEvaluator:
    def __init__(self, data, *, seed=0):
        from solve import distances

        started = perf_counter()
        self.seed = seed
        self.profile = dict(model_seconds=0.0, solve_seconds=0.0, solver_calls=0)
        self.capacities = data['caps']
        self.periods = data['T']
        self.depots = []
        for coords, depot, history in zip(data['coords'], data['depots'], data['hist']):
            matrix = distances(coords, depot)
            penalty = PENALTY_MULTIPLIER * matrix[0, 1:]
            costs = np.rint(matrix * DISTANCE_SCALE).astype(np.int64)
            # Multiplying capacity by T and summing history represents means exactly.
            loads = history.sum(axis=1, dtype=np.int64)
            clients = [Client(*xy, delivery=[int(load)], required=False,
                              prize=int(round(prize * DISTANCE_SCALE)))
                       for xy, load, prize in zip(coords, loads, penalty)]
            self.depots.append((Depot(*depot), clients, matrix, costs, np.zeros_like(costs),
                                float(penalty.sum())))
        self.cost = lru_cache(maxsize=None)(self.cost)
        self.profile['preprocess_seconds'] = perf_counter() - started

    def fleet(self, vehicles):
        return tuple(sorted(self.capacities[v] for v in vehicles))

    def cost(self, depot, fleet, seconds):
        location, clients, _, costs, durations, drop_all = self.depots[depot]
        if not fleet or not clients:
            return drop_all
        started = perf_counter()
        vehicle_types = [VehicleType(capacity=[cap * self.periods]) for cap in fleet]
        problem = ProblemData(clients, [location], vehicle_types, [costs], [durations])
        self.profile['model_seconds'] += perf_counter() - started
        started = perf_counter()
        result = route_solve(problem, MaxRuntime(seconds), seed=self.seed,
                             collect_stats=False, display=False)
        self.profile['solve_seconds'] += perf_counter() - started
        self.profile['solver_calls'] += 1
        if not result.best.is_feasible():
            return drop_all if seconds < 3 else float('inf')

        # All search, comparison and publication paths use the same integer units.
        return float(result.cost() / DISTANCE_SCALE)

    def score(self, allocation, seconds, *, output_order=False):
        total = 0.0
        for depot, vehicles in enumerate(allocation):
            # The scorer creates vehicle types in the order of IDs in the .out file.
            fleet = (tuple(self.capacities[v] for v in sorted(vehicles))
                     if output_order else self.fleet(vehicles))
            total += self.cost(depot, fleet, seconds)
        return total


def fleet_neighbors(groups, capacities):
    for left, right, vehicle, exchange in neighbors(groups, capacities):
        updated = groups.copy()
        updated[left] = [v for v in groups[left] if v != vehicle]
        updated[right] = [v for v in groups[right] if v != exchange] + [vehicle]
        if exchange is not None:
            updated[left].append(exchange)
        yield (min(left, right), max(left, right)), updated


def two_depot_allocations(cap_ids):
    for split in product(*(range(len(ids) + 1) for ids in cap_ids.values())):
        pairs = list(zip(cap_ids.values(), split))
        yield [[v for ids, n in pairs for v in ids[:n]],
               [v for ids, n in pairs for v in ids[n:]]]


def best_allocations(evaluator, seeds, count, rounds, seconds):
    """Use depot additivity to score only new capacity multisets."""
    cap_ids = defaultdict(list)
    for vehicle, cap in enumerate(evaluator.capacities):
        cap_ids[cap].append(vehicle)
    num_depots = len(evaluator.depots)
    size = len(evaluator.capacities)
    candidates = {}

    def add(allocation):
        key = tuple(evaluator.fleet(ids) for ids in allocation)
        candidates.setdefault(key, allocation)
        return key

    for allocation in seeds:
        add(allocation)

    if len(cap_ids) == 1:
        # Keep the k best count splits for each prefix and inventory total.
        cap, ids = next(iter(cap_ids.items()))
        partial = {0: [(0, ())]}
        for depot in range(num_depots):
            updated = defaultdict(list)
            for used, entries in partial.items():
                amounts = range(size - used + 1) if depot + 1 < num_depots else (size - used,)
                for amount in amounts:
                    cost = evaluator.cost(depot, (cap,) * amount, seconds)
                    updated[used + amount].extend((score + cost, split + (amount,))
                                                  for score, split in entries)
            partial = {used: sorted(entries)[:count] for used, entries in updated.items()}
        for _, split in partial[size]:
            boundaries = np.cumsum((0, *split))
            add([ids[lo:hi] for lo, hi in zip(boundaries, boundaries[1:])])
    elif num_depots == 2 and prod(len(ids) + 1 for ids in cap_ids.values()) <= 300:
        for allocation in two_depot_allocations(cap_ids):
            add(allocation)
    else:
        beam = sorted(candidates, key=lambda key: evaluator.score(candidates[key], seconds))[:3]
        expanded = set()
        for _ in range(rounds):
            nearby = set(beam)
            for key in beam:
                if key in expanded:
                    continue
                expanded.add(key)
                for _, updated in fleet_neighbors(candidates[key], evaluator.capacities):
                    nearby.add(add(updated))
            next_beam = sorted(nearby, key=lambda key: (evaluator.score(candidates[key], seconds), key))[:3]
            if next_beam == beam:
                break
            beam = next_beam

    ranked = sorted(candidates.values(), key=lambda allocation: evaluator.score(allocation, seconds))
    return ranked[:count]


def recombine_fleets(evaluator, centers, seconds):
    """Select one scored fleet per depot subject to the shared vehicle inventory."""
    from ortools.linear_solver import pywraplp

    inventory = Counter(evaluator.capacities)
    capacities = sorted(inventory)
    rng = Random(0)
    model = pywraplp.Solver.CreateSolver('SCIP')
    if model is None:
        raise RuntimeError('The score polishing pass requires SCIP support in OR-Tools.')
    model.SetTimeLimit(2000)
    stock = {cap: model.Constraint(count, count) for cap, count in inventory.items()}
    objective = model.Objective()
    objective.SetMinimization()
    columns = []
    for depot in range(len(evaluator.depots)):
        fleets = set()
        for center in centers:
            inside = evaluator.fleet(center[depot])
            outside = sorted((inventory - Counter(inside)).elements())
            fleets.add(inside)
            # One-vehicle additions, removals and exchanges around each center.
            removed = {tuple(sorted((Counter(inside) - Counter([cap])).elements()))
                       for cap in set(inside)}
            fleets.update(removed)
            for base in sorted(removed | {inside}):
                fleets.update(tuple(sorted((*base, cap))) for cap in set(outside))
            # Sample larger changes as additional columns for simultaneous exchanges.
            for _ in range(48):
                remove = rng.randint(0, min(2, len(inside)))
                insert = rng.randint(0, min(2, len(outside)))
                positions = set(rng.sample(range(len(inside)), remove))
                retained = [cap for idx, cap in enumerate(inside) if idx not in positions]
                fleets.add(tuple(sorted(retained + rng.sample(outside, insert))))

        choose_one = model.Constraint(1, 1)
        options = []
        for fleet in sorted(fleets):
            var = model.BoolVar('')
            choose_one.SetCoefficient(var, 1)
            for cap, count in Counter(fleet).items():
                stock[cap].SetCoefficient(var, count)
            objective.SetCoefficient(var, evaluator.cost(depot, fleet, seconds))
            options.append((fleet, var))
        columns.append(options)

    result = []
    for _ in range(12):
        status = model.Solve()
        if status not in (model.OPTIMAL, model.FEASIBLE):
            break
        chosen = [next((fleet, var) for fleet, var in options if var.solution_value() > 0.5)
                  for options in columns]
        # Exclude just this combination to obtain several distinct candidates.
        exclude = model.Constraint(0, len(columns) - 1)
        remaining = {cap: iter([v for v, capacity in enumerate(evaluator.capacities)
                                if capacity == cap]) for cap in capacities}
        result.append([[next(remaining[cap]) for cap in fleet] for fleet, _ in chosen])
        for _, var in chosen:
            exclude.SetCoefficient(var, 1)
    return result


def recheck_neighbors(evaluator, allocation, seconds):
    """Reevaluate promising transfers for every depot pair at the grader's time limit."""
    grouped = defaultdict(list)
    for pair, candidate in fleet_neighbors(allocation, evaluator.capacities):
        grouped[pair].append(candidate)

    shortlisted = []
    for candidates in grouped.values():
        candidates.sort(key=lambda candidate: evaluator.score(candidate, seconds, output_order=True))
        shortlisted.extend(candidates[:3])
    shortlisted.sort(key=lambda candidate: evaluator.score(candidate, 0.4, output_order=True))
    return min([allocation, *shortlisted[:10]],
               key=lambda candidate: evaluator.score(candidate, 3.0, output_order=True))


def optimize(data, seeds, reference, rounds, search_seconds, *, incumbent=None, polish=False,
             validation_trials=3, validation_report=None):
    evaluator = ScoreEvaluator(data)
    if incumbent is None:
        incumbent = reference
    seeds = [incumbent, *seeds]
    recombine = polish and data['D'] > 2 and len(set(data['caps'])) > 1
    if recombine:
        centers = sorted(seeds, key=lambda allocation: evaluator.score(allocation, search_seconds))
        centers = [incumbent, *(center for center in centers if center != incumbent)][:3]
        first_pass = recombine_fleets(evaluator, centers, search_seconds)
        shortlisted = [*seeds, *first_pass]
        if first_pass:
            shortlisted += recombine_fleets(evaluator, first_pass[:2], search_seconds)
    else:
        shortlist_size = 40 if polish else 16
        shortlisted = best_allocations(evaluator, seeds, shortlist_size, rounds, search_seconds)

    distinct = {}
    for allocation in [reference, incumbent, *shortlisted]:
        key = tuple(tuple(evaluator.capacities[v] for v in sorted(ids)) for ids in allocation)
        distinct.setdefault(key, allocation)
    shortlisted = list(distinct.values())
    if len(shortlisted) > 12:
        shortlisted.sort(key=lambda allocation: evaluator.score(allocation, 0.2, output_order=True))
        if polish:
            shortlisted = sorted(shortlisted[:16],
                                 key=lambda allocation: evaluator.score(allocation, 0.65, output_order=True))
        shortlisted = shortlisted[:8 if polish else 6]
    # Limit the expensive validation set, while always retaining both references.
    shortlisted.sort(key=lambda allocation: evaluator.score(allocation, 0.65, output_order=True))
    from validation import validate_candidates
    candidates = {'incumbent': incumbent, 'reference': reference}
    candidates.update({f'search_{i + 1}': groups for i, groups in enumerate(shortlisted[:2])})
    checked = validate_candidates(data, candidates, trials=validation_trials)
    checked['search_profile'] = dict(evaluator.profile)
    if validation_report is not None:
        validation_report.update(checked)
    medians = {row['name']: row['median'] for row in checked['candidates']}
    return (checked['allocation'], medians['reference'], medians['incumbent'],
            medians[checked['selected']])
