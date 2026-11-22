"""Generate diverse feasible routes, pack them globally, then grade allocations.

Route-pool costs are a search heuristic, never a substitute for the three-second
grader: only vehicle assignments are submitted, not the generated routes.
"""
import argparse
from bisect import bisect_left
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import hashlib
from math import ceil
from pathlib import Path
from random import Random

import numpy as np
from ortools.linear_solver import pywraplp
from pyvrp import ProblemData, VehicleType, solve
from pyvrp.stop import MaxIterations, MaxRuntime

from score_optimization import optimize as engine
from result_files import atomic_json

_instance = None


def initialize(path):
    global _instance
    _instance = engine.read_instance(Path(path))
    periods, _, depots, history = _instance
    engine.initialize(periods, depots, history)


def generate_routes(job):
    depot, fleet, iterations, seed, anchor = job
    periods, location, clients, edges, durations, _, _ = engine._depots[depot]
    if not fleet or not clients:
        return depot, anchor, []
    types = ([VehicleType(capacity=[cap * periods]) for cap in fleet] if anchor else
             [VehicleType(num_available=n, capacity=[cap * periods])
              for cap, n in sorted(Counter(fleet).items())])
    problem = ProblemData(clients, [location], types, edges, durations)
    stop = MaxIterations(iterations) if isinstance(iterations, int) else MaxRuntime(iterations)
    result = solve(problem, stop, seed=seed,
                   collect_stats=False, display=False)
    if not result.best.is_feasible():
        return depot, anchor, []
    loads = _instance[3][depot][:, 2:].sum(axis=1, dtype=np.int64)
    rows = []
    for route in result.best.routes():
        visits = tuple(route.visits())
        cost = int(route.distance())
        saving = sum(int(clients[v-1].prize) for v in visits) - cost
        if saving > 0:
            rows.append(dict(depot=depot, nodes=tuple(sorted(visits)),
                             load=int(loads[np.array(visits)-1].sum()),
                             distance=cost, saving=saving))
    return depot, anchor, rows


def assign_trucks(routes, capacities, periods, depot_count, original, demands,
                  *, matching='smallest', spares='original', seed=0):
    """Match actual vehicle IDs to route loads using nested capacity eligibility."""
    rng = Random(seed)
    remaining = list(range(len(capacities)))
    groups = [[] for _ in range(depot_count)]
    owner = {v: d for d, ids in enumerate(original) for v in ids}
    for route in sorted(routes, key=lambda r: -r['load']):
        eligible = [v for v in remaining if capacities[v] * periods >= route['load']]
        if not eligible:
            raise ValueError('Route set cannot be matched to the available fleet.')
        if matching == 'smallest':
            vehicle = min(eligible, key=lambda v: (capacities[v], v))
        elif matching == 'largest':
            vehicle = max(eligible, key=lambda v: (capacities[v], -v))
        else:
            vehicle = rng.choice(eligible)
        groups[route['depot']].append(vehicle)
        remaining.remove(vehicle)
    for vehicle in sorted(remaining, key=lambda v: -capacities[v]):
        if spares == 'original':
            depot = owner[vehicle]
        elif spares == 'balance':
            deficits = [demands[d] - periods * sum(capacities[v] for v in ids)
                        for d, ids in enumerate(groups)]
            depot = max(range(depot_count), key=deficits.__getitem__)
        else:
            depot = int(spares)
        groups[depot].append(vehicle)
    return tuple(tuple(sorted(ids)) for ids in groups)


def pack_routes(columns, capacities, periods, anchor_keys, *, seconds=12, solutions=6):
    """Maximum-saving route packing with exact nested vehicle matching constraints."""
    model = pywraplp.Solver.CreateSolver('SCIP')
    if model is None:
        raise RuntimeError('SCIP is required for route-pool optimization.')
    model.SetTimeLimit(int(seconds * 1000))
    model.SetNumThreads(1)
    levels = sorted(set(capacities))
    stock = [model.Constraint(-model.infinity(), sum(c >= level for c in capacities))
             for level in levels]
    coverage = {}
    objective = model.Objective()
    objective.SetMaximization()
    decisions = []
    for route in columns:
        var = model.BoolVar('')
        for node in route['nodes']:
            key = (route['depot'], node)
            if key not in coverage:
                coverage[key] = model.Constraint(0, 1)
            coverage[key].SetCoefficient(var, 1)
        level = bisect_left([c * periods for c in levels], route['load'])
        if level == len(levels):
            var.SetBounds(0, 0)
        else:
            for constraint in stock[:level+1]:
                constraint.SetCoefficient(var, 1)
        objective.SetCoefficient(var, route['saving'])
        decisions.append(var)
    model.SetHint(decisions, [int((r['depot'], r['nodes']) in anchor_keys) for r in columns])
    plans = []
    for _ in range(solutions):
        status = model.Solve()
        if status not in (model.OPTIMAL, model.FEASIBLE):
            break
        selected = [i for i, var in enumerate(decisions) if var.solution_value() > .5]
        if not selected:
            break
        plans.append([columns[i] for i in selected])
        print(f'  route packing: {len(selected)} routes, pool saving {objective.Value()/1000:.3f}', flush=True)
        exclude = model.Constraint(-model.infinity(), len(selected)-1)
        for i in selected:
            exclude.SetCoefficient(decisions[i], 1)
    return plans


def jobs_for(instance, references, iterations, seeds, level_count=6):
    periods, capacities, locations, history = instance
    levels = sorted(set(capacities))
    indices = np.linspace(0, len(levels)-1, min(level_count, len(levels)), dtype=int)
    sample_levels = [levels[i] for i in indices]
    jobs = []
    for depot, records in enumerate(history):
        # Keep a genuine grading-time incumbent route set as a feasible MIP hint.
        ordered_fleet = tuple(capacities[v] for v in references[0][depot])
        jobs.append((depot, ordered_fleet, 3.0, 0, True))
        demand = float(records[:, 2:].sum()) / periods
        fleets = {tuple(sorted(capacities[v] for v in groups[depot])) for groups in references}
        for cap in sample_levels:
            count = max(1, ceil(demand/cap))
            if count <= len(capacities):
                fleets.add((cap,) * count)
                fleets.add((cap,) * min(len(capacities), count + 2))
        for fleet in sorted(fleets):
            for seed in range(seeds):
                jobs.append((depot, fleet, iterations, seed * 19, False))
    return jobs


def search(path, outdir, references, args):
    instance = engine.read_instance(path)
    periods, caps, locations, history = instance
    existing = [engine.read_allocation(folder/(path.stem+'.out'), len(locations), len(caps))
                for folder in references if (folder/(path.stem+'.out')).is_file()]
    if not existing:
        raise ValueError('Provide at least one legal reference allocation.')
    candidates = list(existing)
    outdir.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(args.workers, initializer=initialize, initargs=(str(path),)) as pool:
        pool_budget = args.pool_iterations or args.pool_seconds
        jobs = jobs_for(instance, existing, pool_budget, args.seeds, args.capacity_levels)
        print(f'{path.stem}: generating routes from {len(jobs)} fleet/seed models', flush=True)
        columns, anchors = {}, set()
        signature = hashlib.sha256(path.read_bytes()+json.dumps(jobs).encode()).hexdigest()
        checkpoint = outdir/'_route_cache.json'
        completed = 0
        if checkpoint.is_file():
            cached = json.loads(checkpoint.read_text(encoding='utf-8'))
            if cached['signature'] == signature:
                completed = cached['completed']
                for route in cached['columns']:
                    route['nodes'] = tuple(route['nodes'])
                    columns[route['depot'], route['nodes']] = route
                anchors = {(d, tuple(nodes)) for d, nodes in cached['anchors']}
                print(f'  resuming {completed} cached route models', flush=True)
        for n, (depot, anchor, routes) in enumerate(pool.map(generate_routes, jobs[completed:]), completed+1):
            for route in routes:
                key = depot, route['nodes']
                if anchor:
                    anchors.add(key)
                if key not in columns or route['distance'] < columns[key]['distance']:
                    columns[key] = route
            if n % 10 == 0 or n == len(jobs):
                atomic_json(checkpoint, dict(signature=signature, completed=n,
                            columns=list(columns.values()), anchors=list(anchors)))
                print(f'  route models {n}/{len(jobs)}, unique routes {len(columns)}', flush=True)
        print(f'  packing {len(columns)} unique route columns', flush=True)
        plans = pack_routes(list(columns.values()), caps, periods, anchors,
                            seconds=args.mip_seconds, solutions=args.solutions)
        demands = [int(records[:, 2:].sum()) for records in history]
        for i, plan in enumerate(plans):
            for matching in ['smallest', 'largest', 'random']:
                for spares in ['original', 'balance', *map(str, range(len(locations)))]:
                    candidates.append(assign_trucks(plan, caps, periods, len(locations),
                                      existing[0], demands, matching=matching,
                                      spares=spares, seed=i*101+args.seed))
        candidates = list(dict.fromkeys(candidates))
        if args.candidate_limit and len(candidates) > args.candidate_limit:
            references_set = set(existing)
            proposals = [groups for groups in candidates if groups not in references_set]
            Random(args.seed).shuffle(proposals)
            candidates = list(dict.fromkeys(existing)) + proposals[:max(0,args.candidate_limit-len(references_set))]
        print(f'  screening {len(candidates)} distinct allocations', flush=True)
        evaluator = engine.Evaluator(instance, pool)
        screen_budget = args.screen_iterations or args.screen_seconds
        final_budget = args.final_iterations or args.final_seconds
        for budget, keep in [(screen_budget, 12), (final_budget, args.finalists)]:
            ranked = evaluator.rank(candidates, budget, keep)
            candidates = [groups for _, groups in ranked]
            print(f'  allocation screen budget {budget}: {len(candidates)} retained, best {ranked[0][0]:.3f}', flush=True)
        if args.defer_grading:
            deferred = evaluator.rank([existing[0], *candidates], final_budget)
    # Leave concurrent search workers before the grading-time comparison.
    if args.defer_grading:
        graded = deferred
    else:
        initialize(str(path))
        graded = []
        distinct = list(dict.fromkeys([existing[0], *candidates]))
        for groups in distinct:
            cost = sum(engine.measure((d, tuple(caps[v] for v in ids), 3.0))
                       for d, ids in enumerate(groups))
            graded.append((cost, groups))
            print(f'  sequential three-second candidate: {cost:.3f}', flush=True)
    graded.sort()
    outdir.mkdir(parents=True, exist_ok=True)
    engine.write_allocation(outdir/(path.stem+'.out'), graded[0][1])
    for i, (cost, groups) in enumerate(graded[:args.finalists], 1):
        engine.write_allocation(outdir/f'rank_{i}'/(path.stem+'.out'), groups)
    atomic_json(outdir/(path.stem+'.json'), dict(
        method='diverse_native_route_pool_and_global_vehicle_matching',
        models=len(jobs), columns=len(columns), route_plans=len(plans),
        grading='search_screen_only' if args.defer_grading else 'single_three_second_trial',
        candidates=[dict(score=cost, allocation=groups) for cost, groups in graded],
        note='Search scores are single trials; publish_results.py must validate independently.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--reference', type=Path, action='append', required=True)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--pool-iterations', type=int)
    parser.add_argument('--screen-iterations', type=int)
    parser.add_argument('--final-iterations', type=int)
    parser.add_argument('--pool-seconds', type=float, default=0.8)
    parser.add_argument('--screen-seconds', type=float, default=0.1)
    parser.add_argument('--final-seconds', type=float, default=0.6)
    parser.add_argument('--capacity-levels', type=int, default=6)
    parser.add_argument('--candidate-limit', type=int, default=32,
                        help='Maximum distinct allocations to screen; 0 disables the cap')
    parser.add_argument('--finalists', type=int, default=4)
    parser.add_argument('--seeds', type=int, default=1)
    parser.add_argument('--mip-seconds', type=float, default=6)
    parser.add_argument('--solutions', type=int, default=4)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--defer-grading', action='store_true',
                        help='Keep finalists for a later isolated publish_results.py validation')
    args = parser.parse_args()
    budgets = [args.pool_seconds, args.screen_seconds, args.final_seconds, args.mip_seconds]
    iterations = [x for x in [args.pool_iterations, args.screen_iterations, args.final_iterations] if x is not None]
    if min(args.workers, args.finalists, args.seeds, args.solutions, args.capacity_levels,
           *budgets, *iterations) <= 0 or args.candidate_limit < 0:
        parser.error('Search counts and budgets must be positive.')
    search(args.input, args.outdir, args.reference, args)


if __name__ == '__main__':
    main()
