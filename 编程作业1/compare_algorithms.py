"""Compare fleet searches on the same mean-demand, three-second PyVRP score."""

import argparse
import csv
from collections import Counter, defaultdict
from math import exp, isfinite, prod
from pathlib import Path
from random import Random

import solve
from score_search import (DISTANCE_SCALE, PENALTY_MULTIPLIER, ScoreEvaluator,
                          best_allocations, fleet_neighbors, recombine_fleets,
                          two_depot_allocations)

import numpy as np
from ortools.linear_solver import pywraplp
from pyvrp import ProblemData, VehicleType, solve as route_solve
from pyvrp.stop import MaxRuntime


class GraderEvaluator(ScoreEvaluator):
    """Use PyVRP's cost directly, as getScore.py does, in the compatible units."""
    # Kept as a public name for existing experiment scripts.
    pass


def ranked(evaluator, candidates, seconds, keep):
    distinct = {}
    for candidate in candidates:
        fleet = tuple(tuple(evaluator.capacities[v] for v in sorted(ids)) for ids in candidate)
        distinct.setdefault(fleet, candidate)
    return sorted(distinct.values(),
                  key=lambda candidate: evaluator.score(candidate, seconds, output_order=True))[:keep]


def as_assignment(allocation, size):
    assignment = [0] * size
    for depot, vehicles in enumerate(allocation):
        for vehicle in vehicles:
            assignment[vehicle] = depot
    return assignment


def as_groups(assignment, num_depots):
    groups = [[] for _ in range(num_depots)]
    for vehicle, depot in enumerate(assignment):
        groups[depot].append(vehicle)
    return groups


def mutate(assignment, num_depots, rng, moves=1):
    candidate = assignment.copy()
    if num_depots <= 1 or not candidate:
        return candidate
    for _ in range(moves):
        vehicle = rng.randrange(len(candidate))
        depot = candidate[vehicle]
        target = (depot + rng.randrange(1, num_depots)) % num_depots
        others = [idx for idx, owner in enumerate(candidate) if owner == target]
        if others and rng.random() < 0.65:
            candidate[rng.choice(others)] = depot
        candidate[vehicle] = target
    return candidate


def anneal(evaluator, centers, seconds, steps, temperature, seed):
    rng = Random(seed)
    depots, size = len(evaluator.depots), len(evaluator.capacities)
    start = ranked(evaluator, centers, seconds, 1)[0]
    assignment = as_assignment(start, size)
    score = evaluator.score(start, seconds, output_order=True)
    cooling = max(score * temperature, 1)
    visited = [start]
    for step in range(steps):
        neighbor = mutate(assignment, depots, rng, moves=2 if rng.random() < 0.22 else 1)
        groups = as_groups(neighbor, depots)
        cost = evaluator.score(groups, seconds, output_order=True)
        limit = cooling * 0.03 ** (step / steps)
        if cost <= score or rng.random() < exp((score - cost) / limit):
            assignment, score = neighbor, cost
        visited.append(groups)
    return ranked(evaluator, visited, seconds, 16)


def evolve(evaluator, centers, seconds, width, generations, seed):
    rng = Random(seed)
    depots, size = len(evaluator.depots), len(evaluator.capacities)
    pool = ranked(evaluator, centers, seconds, width)
    visited = list(pool)
    for _ in range(generations):
        parents = [as_assignment(groups, size) for groups in pool]
        children = []
        for _ in range(width * 2):
            left, right = rng.choice(parents), rng.choice(parents)
            keep = {d for d in range(depots) if rng.random() < 0.4}
            child = [a if a in keep else b for a, b in zip(left, right)]
            child = mutate(child, depots, rng, moves=1 if rng.random() < 0.7 else 3)
            children.append(as_groups(child, depots))
        visited.extend(children)
        pool = ranked(evaluator, [*pool, *children], seconds, width)
    return ranked(evaluator, visited, seconds, 16)


def route_packing(data, offsets, limit):
    """Jointly allocate capacity classes and select nonoverlapping sweep routes."""
    model = pywraplp.Solver.CreateSolver('SCIP')
    if model is None:
        raise RuntimeError('SCIP support is required for the route-packing experiment.')
    model.SetTimeLimit(limit * 1000)
    capacities = Counter(data['caps'])
    classes = sorted(capacities)
    scaled = {**data, 'caps': [cap * data['T'] for cap in data['caps']]}
    demands = [hist.sum(axis=1, dtype=np.int64)[:, None] for hist in data['hist']]
    matrices = [solve.distances(xy, depot) for xy, depot in zip(data['coords'], data['depots'])]
    pools = solve.route_pool(scaled, demands, matrices, offsets, penalty_multiplier=PENALTY_MULTIPLIER)

    inventory = [model.Constraint(capacities[cap], capacities[cap]) for cap in classes]
    counts = []
    objective = model.Objective()
    objective.SetMaximization()
    for depot in range(data['D']):
        row = [model.IntVar(0, capacities[cap], '') for cap in classes]
        counts.append(row)
        for stock, var in zip(inventory, row):
            stock.SetCoefficient(var, 1)
        coverage = [model.Constraint(0, 1) for _ in data['coords'][depot]]
        thresholds = [model.Constraint(-model.infinity(), 0) for _ in classes]
        for level, threshold in enumerate(thresholds):
            for var in row[level:]:
                threshold.SetCoefficient(var, -1)
        for nodes, level, saving in pools[depot, 0]:
            route = model.BoolVar('')
            objective.SetCoefficient(route, saving)
            for node in nodes:
                coverage[node - 1].SetCoefficient(route, 1)
            for threshold in thresholds[:level + 1]:
                threshold.SetCoefficient(route, 1)
    if model.Solve() not in (model.OPTIMAL, model.FEASIBLE):
        return None
    ids_by_cap = defaultdict(list)
    for vehicle, cap in enumerate(data['caps']):
        ids_by_cap[cap].append(vehicle)
    stock = {cap: iter(vehicles) for cap, vehicles in ids_by_cap.items()}
    return [[next(stock[cap]) for cap, count in zip(classes, row)
             for _ in range(round(count.solution_value()))] for row in counts]


def count_refinement(evaluator, centers, width):
    """Optimize count choices jointly using full-duration scores around good splits."""
    cap = evaluator.capacities[0]
    total = len(evaluator.capacities)
    partial = {0: (0.0, ())}
    for depot in range(len(evaluator.depots)):
        options = {amount for center in centers
                   for amount in range(max(0, len(center[depot]) - width),
                                       min(total, len(center[depot]) + width) + 1)}
        updated = {}
        for used, (score, split) in partial.items():
            for amount in sorted(options):
                if used + amount > total:
                    continue
                value = (score + evaluator.cost(depot, (cap,) * amount, 3.0), split + (amount,))
                if used + amount not in updated or value < updated[used + amount]:
                    updated[used + amount] = value
        partial = updated
    if total not in partial:
        return None
    cuts = np.cumsum((0, *partial[total][1]))
    return [list(range(lo, hi)) for lo, hi in zip(cuts, cuts[1:])]


def combine_measured_fleets(evaluator, candidates):
    """Combine already evaluated depot fleets while preserving actual vehicle IDs."""
    model = pywraplp.Solver.CreateSolver('SCIP')
    if model is None:
        raise RuntimeError('SCIP support is required for the measured-fleet experiment.')
    model.SetTimeLimit(3000)
    inventory = [model.Constraint(1, 1) for _ in evaluator.capacities]
    objective = model.Objective()
    objective.SetMinimization()
    choices = []
    for depot in range(len(evaluator.depots)):
        depot_constraint = model.Constraint(1, 1)
        choices.append([])
        for vehicles in sorted({tuple(sorted(candidate[depot])) for candidate in candidates}):
            fleet = tuple(evaluator.capacities[v] for v in vehicles)
            score = evaluator.cost(depot, fleet, 3.0)
            if not isfinite(score):
                continue
            var = model.BoolVar('')
            objective.SetCoefficient(var, score)
            depot_constraint.SetCoefficient(var, 1)
            for vehicle in vehicles:
                inventory[vehicle].SetCoefficient(var, 1)
            choices[-1].append((vehicles, var))
    if model.Solve() not in (model.OPTIMAL, model.FEASIBLE):
        return None
    return [list(next(ids for ids, var in row if var.solution_value() > 0.5)) for row in choices]


def benchmark(data, published, *, evaluator=None):
    if evaluator is None:
        evaluator = GraderEvaluator(data)
    if data['D'] == 1:
        groups = [list(range(data['V']))]
        score = evaluator.score(groups, 3.0, output_order=True)
        return {'published': (published, score), 'single_depot': (groups, score)}
    short = 0.045
    demand_seeds = [solve.baseline(data, data['hist'], kind) for kind in ('mean', 'risk', 'balanced')]
    seeds = [published, *demand_seeds]
    methods = {'published': [published], 'demand_greedy': demand_seeds}
    homogeneous = len(set(data['caps'])) == 1
    mixed_many_depots = data['D'] > 2 and not homogeneous
    methods['count_dp' if homogeneous else ('beam' if mixed_many_depots else 'enumeration')] = (
        best_allocations(evaluator, seeds, 42, 4, short)
    )
    print('  counts / beam complete', flush=True)
    if mixed_many_depots:
        local = ranked(evaluator, best_allocations(evaluator, seeds, 9, 2, short), short, 2)
        centers = [published, *local]
        methods['fleet_mip'] = recombine_fleets(evaluator, centers, short)
    else:
        centers = seeds

    if not homogeneous:
        methods['anneal_cool'] = anneal(evaluator, centers, short, 210, 0.006, 91)
        methods['anneal_warm'] = anneal(evaluator, centers, short, 350, 0.020, 73)
        methods['genetic'] = evolve(evaluator, centers, short, 8, 14, 101)
        methods['route_mip'] = [allocation for offsets, seconds in ((2, 8), (4, 16))
                                if (allocation := route_packing(data, offsets, seconds)) is not None]
        print('  stochastic searches and route packing complete', flush=True)

    final = {}
    for name, candidates in methods.items():
        if not candidates:
            continue
        first = ranked(evaluator, candidates, 0.22, 14)
        second = ranked(evaluator, first, 0.65, 5 if name in ('enumeration', 'count_dp', 'beam') else 2)
        if homogeneous and data['D'] == 2 and name == 'count_dp':
            second = candidates
        final[name] = ranked(evaluator, second, 3.0, 1)[0]
        print(f'  {name}: {evaluator.score(final[name], 3.0, output_order=True):.3f}', flush=True)

    # Align the bounds of the hybrid stage with the final scoring runtime.
    anchors = ranked(evaluator, final.values(), 3.0, 3)
    extra = []
    if homogeneous and data['D'] > 2:
        allocation = count_refinement(evaluator, anchors[:2], width=2)
        if allocation is not None:
            extra.append(allocation)
    elif not homogeneous:
        neighbors = defaultdict(list)
        for anchor in anchors[:2]:
            for pair, allocation in fleet_neighbors(anchor, data['caps']):
                neighbors[pair].append(allocation)
        shortlist = [candidate for group in neighbors.values()
                     for candidate in ranked(evaluator, group, short, 5)]
        extra = ranked(evaluator, shortlist, 0.55, 6)
    final['hybrid'] = ranked(evaluator, [*anchors, *extra], 3.0, 1)[0]
    return {method: (allocation, evaluator.score(allocation, 3.0, output_order=True))
            for method, allocation in final.items()}


def deeper_benchmark(data, published, *, evaluator=None):
    if evaluator is None:
        evaluator = GraderEvaluator(data)
    cap_ids = defaultdict(list)
    for vehicle, cap in enumerate(data['caps']):
        cap_ids[cap].append(vehicle)
    if len(cap_ids) == 1:
        method = 'full_count_dp'
        finalists = [published, count_refinement(evaluator, [published], data['V'])]
    elif data['D'] == 2 and prod(len(ids) + 1 for ids in cap_ids.values()) <= 300:
        method = 'deep_enumeration'
        finalists = [published, *two_depot_allocations(cap_ids)]
        for seconds, keep in ((0.14, 56), (0.42, 20), (1.1, 10)):
            finalists = ranked(evaluator, finalists, seconds, keep)
            print(f'  {seconds:.2f}s: {len(finalists)} finalists', flush=True)
        finalists.append(published)
    else:
        raise ValueError('Deep enumeration needs homogeneous vehicles or a small two-depot fleet.')

    winner = ranked(evaluator, finalists, 3.0, 1)[0]
    return {
        'published': (published, evaluator.score(published, 3.0, output_order=True)),
        method: (winner, evaluator.score(winner, 3.0, output_order=True)),
    }


def wide_local_benchmark(data, published, *, evaluator=None):
    if evaluator is None:
        evaluator = GraderEvaluator(data)
    by_pair = defaultdict(list)
    for pair, allocation in fleet_neighbors(published, data['caps']):
        by_pair[pair].append(allocation)
    intermediate = [ranked(evaluator, candidates, 0.08, 5) for candidates in by_pair.values()]
    finalists = [published]
    medium = [ranked(evaluator, group, 0.42, 2) for group in intermediate]
    finalists.extend(group[0] for group in medium)
    finalists.extend(ranked(evaluator, [candidate for group in medium for candidate in group], 0.42, 6))
    print(f'  evaluating {len(finalists)} finalists across {len(by_pair)} depot pairs', flush=True)
    shortlisted = ranked(evaluator, finalists, 3.0, len(finalists))
    merged = combine_measured_fleets(evaluator, shortlisted)
    if merged is not None:
        shortlisted.append(merged)
    best = ranked(evaluator, shortlisted, 3.0, 1)[0]
    return {
        'published': (published, evaluator.score(published, 3.0, output_order=True)),
        'wide_local_mip': (best, evaluator.score(best, 3.0, output_order=True)),
    }


def portfolio(data, published, confirmed=None):
    """Run the applicable searches and keep the lowest three-second score."""
    evaluator = GraderEvaluator(data)
    outcomes = benchmark(data, published, evaluator=evaluator)
    inventory = Counter(data['caps'])
    if data['D'] == 2 and len(inventory) > 1 and prod(n + 1 for n in inventory.values()) <= 300:
        outcomes.update(deeper_benchmark(data, published, evaluator=evaluator))
    elif data['D'] > 2:
        if len(inventory) == 1:
            outcomes.update(deeper_benchmark(data, published, evaluator=evaluator))
        else:
            outcomes.update(wide_local_benchmark(data, published, evaluator=evaluator))

    if confirmed is not None:
        outcomes['confirmed_incumbent'] = (confirmed, evaluator.score(confirmed, 3.0, output_order=True))
    if data['D'] > 2 and len(inventory) > 1:
        leaders = ranked(evaluator, [groups for groups, _ in outcomes.values()], 3.0, 5)
        combined = combine_measured_fleets(evaluator, leaders)
        if combined is not None:
            outcomes['measured_recombination'] = (combined, evaluator.score(combined, 3.0, output_order=True))
    outcomes['portfolio'] = min(outcomes.values(), key=lambda outcome: outcome[1])
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--deeper', action='store_true', help='Increase budgets for count enumeration')
    mode.add_argument('--wide-local', action='store_true', help='Recombine full-duration fleet scores')
    mode.add_argument('--portfolio', action='store_true', help='Run the full mixed search and select its best fleet')
    args = parser.parse_args()
    data = solve.read_instance(args.file)
    original = solve.HERE / 'score_output' / args.file.with_suffix('.out').name
    repository_input = (solve.HERE / 'input_data' / args.file.name).resolve()
    reference_matches = args.file.resolve() == repository_input and original.is_file()
    published = (solve.read_allocation(original, data) if reference_matches
                 else solve.baseline(data, data['hist'], 'mean'))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    search = deeper_benchmark if args.deeper else benchmark
    if args.wide_local:
        search = wide_local_benchmark
    if data['D'] == 1:
        outcomes = benchmark(data, published)
    elif args.portfolio:
        confirmed_file = solve.HERE / 'score_benchmark' / original.name
        confirmed = (solve.read_allocation(confirmed_file, data)
                     if reference_matches and confirmed_file.is_file() else None)
        outcomes = portfolio(data, published, confirmed)
    else:
        outcomes = search(data, published)
    destination = args.output_dir / args.file.with_suffix('.out').name
    with destination.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['input', 'method', 'score', 'allocation_file'])
        for method, (allocation, score) in outcomes.items():
            solve.check_allocation(data, allocation)
            output = destination.with_stem(f'{destination.stem}_{method}')
            solve.write_allocation(output, allocation)
            writer.writerow([args.file.name, method, score if isfinite(score) else 'inf', output.name])
    print(f'{args.file.name}: {outcomes["published"][1]:.3f} -> {min(score for _, score in outcomes.values()):.3f}',
          flush=True)


if __name__ == '__main__':
    main()
