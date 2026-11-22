"""Tune legal vehicle ordering for the fixed three-second routing heuristic.

Vehicle ownership and capacities stay fixed. Only the input order of vehicle
types changes; final results must pass fresh, paired validation before release.
"""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from math import factorial
from pathlib import Path
from random import Random

from score_optimization import optimize as engine
from result_files import atomic_json


def candidate_orders(ids, capacities, count=10, seed=0):
    ids = tuple(ids)
    rng = Random(seed)
    by_cap = defaultdict(list)
    for vehicle in ids:
        by_cap[capacities[vehicle]].append(vehicle)

    def canonical(order):
        available = {cap: iter(sorted(vehicles)) for cap, vehicles in by_cap.items()}
        return tuple(next(available[capacities[v]]) for v in order)

    orders = {}
    for order in [ids, ids[::-1], sorted(ids, key=lambda v: capacities[v]),
                  sorted(ids, key=lambda v: -capacities[v])]:
        normalized = canonical(order)
        orders.setdefault(tuple(capacities[v] for v in normalized), normalized)
    total = factorial(len(ids))
    for n in Counter(capacities[v] for v in ids).values():
        total //= factorial(n)
    for _ in range(count * 100):
        if len(orders) >= min(count, total):
            break
        order = list(ids)
        rng.shuffle(order)
        normalized = canonical(order)
        orders.setdefault(tuple(capacities[v] for v in normalized), normalized)
    return list(orders.values())[:count]


def search(path, allocation_file, outdir, *, samples=8, screen_seconds=0.08, seed=17,
           direct=False):
    instance = engine.read_instance(path)
    periods, caps, locations, history = instance
    original = engine.read_allocation(allocation_file, len(locations), len(caps))
    engine.initialize(periods, locations, history)
    outdir.mkdir(parents=True, exist_ok=True)
    cache_path = outdir/'_order_cache.json'
    signature = hashlib.sha256(path.read_bytes()+allocation_file.read_bytes()).hexdigest()
    cached = {}
    if cache_path.is_file():
        saved = json.loads(cache_path.read_text(encoding='utf-8'))
        if saved['signature'] == signature:
            cached = saved['values']
    per_depot, records = [], []
    for depot, ids in enumerate(original):
        choices = candidate_orders(ids, caps, samples, seed+depot*31)
        if direct:
            finalists = choices
        else:
            screened = sorted((engine.measure((depot, tuple(caps[v] for v in order), screen_seconds)), order)
                              for order in choices)
            finalists = list(dict.fromkeys([ids, *(order for _, order in screened[:3])]))
        graded = []
        for n, order in enumerate(finalists, 1):
            key = str(depot)+':'+','.join(map(str,order))
            if key not in cached:
                cached[key] = engine.measure((depot, tuple(caps[v] for v in order), 3.0))
                atomic_json(cache_path, dict(signature=signature, values=cached))
            value = cached[key]
            graded.append((value, order))
            if direct and n % 5 == 0:
                print(f'  depot {depot}: {n}/{len(finalists)} orders, best {min(x[0] for x in graded):.3f}', flush=True)
        graded.sort()
        per_depot.append(graded)
        records.append(dict(depot=depot, choices=len(choices),
                            candidates=[dict(score=score, vehicles=order) for score, order in graded]))
        print(f'{path.stem} depot {depot}: legal order search best {graded[0][0]:.3f}', flush=True)
    for rank in range(3):
        allocation = tuple(row[min(rank,len(row)-1)][1] for row in per_depot)
        engine.write_allocation(outdir/f'rank_{rank+1}'/(path.stem+'.out'), allocation)
        if rank == 0:
            engine.write_allocation(outdir/(path.stem+'.out'), allocation)
    atomic_json(outdir/(path.stem+'.json'), dict(method='legal_vehicle_type_ordering',
                 unchanged_ownership=True, input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                 search_signature=signature, depots=records,
                 note='Three-second single-trial selection; validate independently before release.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--allocation', type=Path, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=8)
    parser.add_argument('--screen-seconds', type=float, default=0.08)
    parser.add_argument('--direct', action='store_true',
                        help='Evaluate every sampled order at the actual three-second budget')
    args = parser.parse_args()
    if args.samples < 1 or args.screen_seconds <= 0:
        parser.error('Sample counts and screen runtime must be positive.')
    search(args.input, args.allocation, args.outdir, samples=args.samples,
           screen_seconds=args.screen_seconds, direct=args.direct)


if __name__ == '__main__':
    main()
