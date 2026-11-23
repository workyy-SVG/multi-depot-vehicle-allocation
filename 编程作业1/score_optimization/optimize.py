"""Search vehicle allocations for the mean-demand prize-collecting VRP."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations, product
from math import exp, isfinite, prod
from pathlib import Path
from random import Random

import numpy as np
from ortools.linear_solver import pywraplp
from pyvrp import Client, Depot, ProblemData, VehicleType, solve
from pyvrp.stop import MaxIterations, MaxRuntime

SCALE = 1000
_depots = []


def read_instance(path):
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    depots, vehicles, periods = map(int, rows[0].split())
    caps = tuple(map(int, rows[1].split()))
    counts = tuple(map(int, rows[2].split()))
    if len(caps) != vehicles or len(counts) != depots or periods <= 0:
        raise ValueError(f"Invalid header in {path}")
    locations = [tuple(map(int, row.split())) for row in rows[3:3 + depots]]
    history = []
    offset = 3 + depots
    for count in counts:
        records = np.array([list(map(int, row.split())) for row in rows[offset:offset + count]],
                           dtype=np.int64).reshape(count, periods + 2)
        history.append(records)
        offset += count
    return periods, caps, locations, history


def read_allocation(path, depots, vehicles):
    rows = [tuple(map(int, row.split())) for row in path.read_text().splitlines()]
    if len(rows) != depots or any(not row or row[0] != len(row) - 1 for row in rows):
        raise ValueError(f"Invalid depot rows in {path}")
    groups = tuple(tuple(row[1:]) for row in rows)
    if sorted(v for group in groups for v in group) != list(range(vehicles)):
        raise ValueError(f"Each vehicle must appear once in {path}")
    return groups


def write_allocation(path, groups):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(" ".join(map(str, (len(group), *group))) + "\n"
                            for group in groups), encoding="ascii")


def initialize(periods, locations, history):
    global _depots
    _depots = []
    for location, records in zip(locations, history):
        points = np.vstack([location, records[:, :2]])
        distance = np.hypot(*(points[:, None] - points[None, :]).transpose(2, 0, 1))
        edges = np.rint(SCALE * distance).astype(np.int64)
        prizes = np.rint(2 * SCALE * distance[0, 1:]).astype(np.int64)
        clients = [Client(int(row[0]), int(row[1]), delivery=[int(row[2:].sum())],
                          required=False, prize=int(prize))
                   for row, prize in zip(records, prizes)]
        _depots.append((periods, Depot(*location), clients, [edges], [np.zeros_like(edges)],
                        float(2 * distance[0].sum()), int(prizes.sum()) / SCALE))


def measure(job):
    depot, fleet, budget = job
    periods, location, clients, edges, durations, no_vehicle, drop_all = _depots[depot]
    if not fleet or not clients:
        return no_vehicle
    types = [VehicleType(capacity=[cap * periods]) for cap in fleet]
    data = ProblemData(clients, [location], types, edges, durations)
    stop = MaxIterations(budget) if isinstance(budget, int) else MaxRuntime(budget)
    result = solve(data, stop, seed=0, collect_stats=False)
    if result.best.is_feasible():
        return result.cost() / SCALE
    return float("inf") if budget == 3.0 and isinstance(budget, float) else drop_all


class Evaluator:
    def __init__(self, instance, executor):
        _, self.caps, self.locations, self.history = instance
        self.executor = executor
        self.cache = {}
        self.id_by_cap = defaultdict(list)
        for vehicle, cap in enumerate(self.caps):
            self.id_by_cap[cap].append(vehicle)
        self.id_by_cap = dict(sorted(self.id_by_cap.items()))
        self.inventory = Counter(self.caps)

    def key(self, allocation):
        return tuple(tuple(self.caps[v] for v in group) for group in allocation)

    def canonical(self, fleets):
        available = {cap: iter(ids) for cap, ids in self.id_by_cap.items()}
        return tuple(tuple(sorted(next(available[cap]) for cap in fleet)) for fleet in fleets)

    def cached_cost(self, depot, fleet, budget):
        return self.cache[depot, fleet, type(budget), budget]

    def costs(self, columns, budget):
        keys = sorted({(depot, fleet, type(budget), budget) for depot, fleet in columns}
                      - self.cache.keys())
        jobs = ((depot, fleet, amount) for depot, fleet, _, amount in keys)
        self.cache.update(zip(keys, self.executor.map(measure, jobs)))

    def rank(self, candidates, budget, keep=None):
        distinct = {}
        for candidate in candidates:
            distinct.setdefault(self.key(candidate), candidate)
        self.costs(((depot, fleet) for key in distinct for depot, fleet in enumerate(key)), budget)
        ranked = [(sum(self.cached_cost(d, fleet, budget) for d, fleet in enumerate(key)), groups)
                  for key, groups in distinct.items()]
        return sorted(ranked)[:keep]


def greedy_seeds(evaluator):
    demand = np.array([records[:, 2:].mean(axis=1).sum() for records in evaluator.history])
    penalty = np.array([np.linalg.norm(records[:, :2] - location, axis=1).sum()
                        for records, location in zip(evaluator.history, evaluator.locations)])
    full = sum(evaluator.caps)
    results = []
    for weights in (np.ones(len(demand)), demand, penalty, np.sqrt(penalty * demand)):
        targets = full * weights / max(1, weights.sum())
        groups = [[] for _ in targets]
        load = np.zeros_like(targets)
        for vehicle in sorted(range(len(evaluator.caps)), key=lambda v: -evaluator.caps[v]):
            cap = evaluator.caps[vehicle]
            depot = int(np.argmax(targets - load))
            groups[depot].append(vehicle)
            load[depot] += cap
        results.append(tuple(tuple(sorted(group)) for group in groups))
    return results


def count_dp(evaluator, budget):
    size = len(evaluator.caps)
    cap = evaluator.caps[0]
    count = len(evaluator.locations)
    evaluator.costs(((d, (cap,) * n) for d in range(count) for n in range(size + 1)), budget)
    partial = {0: (0.0, ())}
    for depot in range(count):
        next_layer = {}
        for used, (score, split) in partial.items():
            amounts = range(size - used + 1) if depot < count - 1 else (size - used,)
            for amount in amounts:
                candidate = (score + evaluator.cached_cost(depot, (cap,) * amount, budget),
                             (*split, (cap,) * amount))
                total = used + amount
                if total not in next_layer or candidate < next_layer[total]:
                    next_layer[total] = candidate
        partial = next_layer
    return evaluator.canonical(partial[size][1])


def enumerated_splits(evaluator):
    counts = evaluator.inventory
    for split in product(*(range(counts[cap] + 1) for cap in evaluator.id_by_cap)):
        left = tuple(cap for cap, n in zip(evaluator.id_by_cap, split) for _ in range(n))
        right = tuple(cap for cap, n in zip(evaluator.id_by_cap, split)
                      for _ in range(counts[cap] - n))
        yield evaluator.canonical((left, right))


def changed(groups, left, right, one, other, *, preserve_order=False):
    updated = list(groups)
    updated[left] = (*[v for v in groups[left] if v not in one], *other)
    updated[right] = (*[v for v in groups[right] if v not in other], *one)
    if not preserve_order:
        updated[left] = tuple(sorted(updated[left]))
        updated[right] = tuple(sorted(updated[right]))
    return tuple(updated)


def paired_neighbors(groups, left, right, rng, samples):
    a, b = groups[left], groups[right]
    for v in a:
        yield changed(groups, left, right, (v,), ())
    for w in b:
        yield changed(groups, left, right, (), (w,))
    for v, w in product(a, b):
        yield changed(groups, left, right, (v,), (w,))
    options = [(n, m) for n in range(min(3, len(a)) + 1)
               for m in range(min(3, len(b)) + 1) if n + m >= 2 and (n, m) != (1, 1)]
    if options:
        for _ in range(samples):
            n, m = rng.choice(options)
            yield changed(groups, left, right, rng.sample(a, n), rng.sample(b, m))


def perturb(groups, rng, strength, *, capacities=None):
    assignment = list(groups)
    for _ in range(strength):
        if capacities is not None and rng.random() < 0.25:
            depots = [d for d, ids in enumerate(assignment) if len({capacities[v] for v in ids}) > 1]
            if depots:
                depot = rng.choice(depots)
                ordered = list(assignment[depot])
                left = rng.randrange(len(ordered))
                right = rng.choice([i for i, v in enumerate(ordered)
                                    if capacities[v] != capacities[ordered[left]]])
                ordered[left], ordered[right] = ordered[right], ordered[left]
                assignment[depot] = tuple(ordered)
                continue
        left, right = rng.sample(range(len(groups)), 2)
        one = (rng.choice(assignment[left]),) if assignment[left] else ()
        other = (rng.choice(assignment[right]),) if assignment[right] and (
            not one or rng.random() < 0.8) else ()
        assignment = list(changed(assignment, left, right, one, other,
                                  preserve_order=capacities is not None))
    return tuple(assignment)


def three_depot_cycles(groups, rng, limit):
    """Try three-way exchanges that preserve each depot's vehicle count."""
    active = [depot for depot, vehicles in enumerate(groups) if vehicles]
    if len(active) < 3:
        return
    for _ in range(limit):
        depots = rng.sample(active, 3)
        vehicles = [rng.choice(groups[depot]) for depot in depots]
        moved = list(groups)
        for index, depot in enumerate(depots):
            remaining = set(groups[depot]) - {vehicles[index]}
            moved[depot] = tuple(sorted(remaining | {vehicles[index - 1]}))
        yield tuple(moved)


def recombine(evaluator, candidates, budget, keep):
    """Select one measured vehicle subset for each depot."""
    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        raise RuntimeError("SCIP is unavailable in the installed OR-Tools")
    solver.SetTimeLimit(2500)
    stock = [solver.Constraint(1, 1) for _ in evaluator.caps]
    objective = solver.Objective()
    objective.SetMinimization()
    columns = []
    for depot, groups in enumerate(zip(*candidates)):
        one = solver.Constraint(1, 1)
        row = {}
        for ids in sorted(set(groups)):
            fleet = tuple(evaluator.caps[v] for v in ids)
            cost = evaluator.cached_cost(depot, fleet, budget)
            if not isfinite(cost):
                continue
            decision = solver.BoolVar("")
            one.SetCoefficient(decision, 1)
            for vehicle in ids:
                stock[vehicle].SetCoefficient(decision, 1)
            objective.SetCoefficient(decision, cost)
            row[ids] = decision
        columns.append(row)
    for _ in range(keep):
        if solver.Solve() not in (solver.OPTIMAL, solver.FEASIBLE):
            break
        groups = tuple(next(ids for ids, var in row.items() if var.solution_value() > .5)
                       for row in columns)
        yield groups
        solver.Add(sum(row[ids] for row, ids in zip(columns, groups)) <= len(columns) - 1)


def record(log, name, ranked, outdir, stem):
    score, groups = ranked[0]
    path = outdir / "candidates" / f"{stem}__{name}.out"
    write_allocation(path, groups)
    log.append((stem, name, score, str(path.relative_to(outdir))))
    print(f"  {name}: {score:.3f}", flush=True)


def heterogeneous(evaluator, references, args):
    methods = {}
    rng = Random(args.seed)
    seeds = [*references, *greedy_seeds(evaluator)]
    methods["demand_greedy"] = seeds[len(references):]
    low, medium, high = args.screen_time or (
        args.low_iterations, args.medium_iterations, args.high_iterations)
    if len(evaluator.locations) == 2 and prod(n + 1 for n in evaluator.inventory.values()) <= 2500:
        all_splits = list(enumerated_splits(evaluator))
        print(f"  enumerating {len(all_splits)} capacity splits", flush=True)
        if args.full_enumeration:
            methods["full_enumeration"] = [
                group for _, group in evaluator.rank(all_splits, 3.0, args.finalists)]
            return methods
        for budget, width in ((low, args.enumeration_width), (medium, args.finalists * 2),
                              (high, args.finalists)):
            all_splits = [group for _, group in evaluator.rank(all_splits, budget, width)]
            print(f"  screening at {budget:g}: retained {len(all_splits)} splits", flush=True)
        methods["staged_enumeration"] = all_splits
        return methods

    leaders = [group for _, group in evaluator.rank(seeds, medium, args.beam)]
    pairs = list(combinations(range(len(evaluator.locations)), 2))
    for depth in range(args.rounds):
        print(f"  neighborhood round {depth + 1}", flush=True)
        by_pair = {pair: [group for center in leaders
                          for group in paired_neighbors(center, *pair, rng,
                                                        0 if depth == 0 else args.samples)]
                   for pair in pairs}
        evaluator.rank((group for candidates in by_pair.values() for group in candidates), low, 1)
        pair_shortlist = [group for candidates in by_pair.values()
                          for _, group in evaluator.rank(candidates, low, args.pair_width)]
        local = evaluator.rank([*leaders, *pair_shortlist], medium, args.finalists * 3)
        methods["transfer_swap" if depth == 0 else f"multi_vehicle_{depth}"] = [
            group for _, group in local[:args.finalists]]

        candidates = [*leaders, *pair_shortlist]
        measured = [group for _, group in evaluator.rank(candidates, medium)]
        joined = list(recombine(evaluator, measured, medium, args.finalists))
        methods[f"fleet_mip_{depth + 1}"] = joined
        leaders = [group for _, group in evaluator.rank(
            [*leaders, *joined, *(group for _, group in local)], medium, args.beam)]

    if args.cycles:
        cycles = [candidate for group in leaders
                  for candidate in three_depot_cycles(group, rng, args.cycles)]
        screened = [group for _, group in evaluator.rank(
            [*leaders, *cycles], low, args.finalists * 2)]
        methods["three_depot_cycles"] = [group for _, group in evaluator.rank(
            screened, medium, args.finalists)]

    ordered_capacities = evaluator.caps if args.anneal_ordered else None
    for temperature in args.temperatures:
        walkers = evaluator.rank(leaders, low, args.beam)
        explored = list(walkers)
        for step in range(args.anneal_steps):
            proposals = [perturb(group, rng, rng.choice((1, 1, 2, 3)), capacities=ordered_capacities)
                         for _, group in walkers]
            scores = dict((group, score) for score, group in evaluator.rank(proposals, low))
            scale = temperature * args.anneal_final_ratio ** (step / max(1, args.anneal_steps - 1))
            for idx, ((score, old), proposal) in enumerate(zip(walkers, proposals)):
                trial = scores.get(proposal)
                if trial is None:
                    trial = evaluator.rank([proposal], low, 1)[0][0]
                if trial <= score or rng.random() < exp((score - trial) / max(1, score * scale)):
                    walkers[idx] = (trial, proposal)
                explored.append((trial, proposal))
        # Take the unique cheap-scored candidates before applying the medium budget.
        distinct = evaluator.rank((group for _, group in explored), low, args.finalists * 5)
        shortlisted = [group for _, group in evaluator.rank(
            (group for _, group in distinct), medium, args.finalists)]
        methods[f"anneal_{temperature:g}"] = shortlisted
    return methods


def optimize(path, outdir, references, args, log):
    instance = read_instance(path)
    periods, caps, locations, history = instance
    seeds = {folder.name: read_allocation(folder / path.with_suffix(".out").name,
                                          len(locations), len(caps))
             for folder in references if (folder / path.with_suffix(".out").name).is_file()}
    print(path.stem, flush=True)
    with ProcessPoolExecutor(args.workers, initializer=initialize,
                             initargs=(periods, locations, history)) as executor:
        evaluator = Evaluator(instance, executor)
        method_groups = {f"reference_{name}": [groups] for name, groups in seeds.items()}
        if len(locations) == 1:
            method_groups["single_depot"] = [(tuple(range(len(caps))),)]
        elif len(set(caps)) == 1:
            method_groups["demand_greedy"] = greedy_seeds(evaluator)
            budgets = (3.0,) if max(map(len, history)) > 500 else (
                args.medium_iterations, args.high_iterations, 3.0)
            for budget in budgets:
                method_groups[f"count_dp_{budget}"] = [count_dp(evaluator, budget)]
        else:
            method_groups.update(heterogeneous(evaluator, list(seeds.values()), args))

        groups = [group for candidates in method_groups.values() for group in candidates]
        evaluator.rank(groups, 3.0, 1)
        for name, candidates in method_groups.items():
            if candidates:
                record(log, name, evaluator.rank(candidates, 3.0, 1), outdir, path.stem)
        if len(locations) > 2 and len(set(caps)) > 1:
            combined = list(recombine(evaluator, groups, 3.0, 2))
            if combined:
                record(log, "measured_mip", evaluator.rank(combined, 3.0, 1), outdir, path.stem)
                groups.extend(combined)
        ranked = evaluator.rank(groups, 3.0)
        record(log, "portfolio", ranked, outdir, path.stem)
        write_allocation(outdir / path.with_suffix(".out").name, ranked[0][1])
        finalists = {str(outdir.joinpath(row[3]).name): row[2] for row in log if row[0] == path.stem}
        for index, (score, group) in enumerate(ranked[:args.finalists]):
            target = outdir / "candidates" / f"{path.stem}__rank_{index + 1}.out"
            write_allocation(target, group)
            finalists[target.name] = score
        (outdir / f"{path.stem}.json").write_text(json.dumps(finalists, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="One .in file or a directory containing .in files")
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--reference", action="append", type=Path, default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--beam", type=int, default=4)
    parser.add_argument("--samples", type=int, default=36)
    parser.add_argument("--pair-width", type=int, default=8)
    parser.add_argument("--finalists", type=int, default=12)
    parser.add_argument("--enumeration-width", type=int, default=96)
    parser.add_argument("--low-iterations", type=int, default=36)
    parser.add_argument("--medium-iterations", type=int, default=180)
    parser.add_argument("--high-iterations", type=int, default=700)
    parser.add_argument("--screen-time", type=float, nargs=3, metavar=("LOW", "MEDIUM", "HIGH"),
                        help="Use wall-clock seconds instead of iteration budgets for screening")
    parser.add_argument("--full-enumeration", action="store_true",
                        help="Evaluate every small two-depot capacity split for three seconds")
    parser.add_argument("--anneal-steps", type=int, default=32)
    parser.add_argument("--anneal-final-ratio", type=float, default=0.025,
                        help="Final geometric temperature as a fraction of the initial temperature")
    parser.add_argument("--anneal-ordered", action="store_true",
                        help="Keep sequences on transfers and also sample within-depot order swaps")
    parser.add_argument("--cycles", type=int, default=0,
                        help="Number of three-way exchanges sampled from each leading allocation")
    parser.add_argument("--temperatures", type=float, nargs="*", default=(0.002, 0.008))
    args = parser.parse_args()
    if min(args.workers, args.beam, args.pair_width, args.finalists, args.enumeration_width) < 1:
        parser.error("Worker and shortlist sizes must be positive")
    if min(args.rounds, args.samples, args.cycles, args.anneal_steps) < 0:
        parser.error("Search rounds and sample counts must be nonnegative")
    if not 0 < args.anneal_final_ratio <= 1:
        parser.error("Final temperature ratio must be in (0, 1]")
    if any(not isfinite(t) or t <= 0 for t in args.temperatures):
        parser.error("Initial temperatures must be positive and finite")
    if args.screen_time and not 0 < args.screen_time[0] <= args.screen_time[1] <= args.screen_time[2]:
        parser.error("Screening times must be positive and in ascending order")
    paths = sorted(args.input.glob("*.in")) if args.input.is_dir() else [args.input]
    if not paths:
        parser.error("No .in files found")
    args.outdir.mkdir(parents=True, exist_ok=True)
    log = []
    with (args.outdir / "search.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("instance", "method", "three_second_search_score", "file"))
        for path in paths:
            start = len(log)
            optimize(path, args.outdir, args.reference, args, log)
            writer.writerows(log[start:])
            stream.flush()


if __name__ == "__main__":
    main()
