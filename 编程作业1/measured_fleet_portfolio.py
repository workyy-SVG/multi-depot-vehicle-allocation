"""Recombine measured ordered fleets by capacity inventory, independent of IDs."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from math import isfinite
from ortools.linear_solver import pywraplp


def combine_fleets(capacities, columns, *, solutions=3, seconds=5):
    """columns[d] maps an ordered capacity tuple to its measured routing cost."""
    model = pywraplp.Solver.CreateSolver('SCIP')
    if model is None:
        raise RuntimeError('SCIP is required for measured-fleet recombination.')
    model.SetTimeLimit(int(seconds*1000))
    inventory = Counter(capacities)
    stock = {cap:model.Constraint(count,count) for cap,count in inventory.items()}
    objective = model.Objective()
    objective.SetMinimization()
    options = []
    for depot, fleets in enumerate(columns):
        choose = model.Constraint(1,1)
        row=[]
        for fleet,cost in fleets.items():
            if not isfinite(cost):continue
            counts=Counter(fleet)
            if counts-inventory:continue
            var=model.BoolVar('')
            choose.SetCoefficient(var,1)
            for cap,count in counts.items():stock[cap].SetCoefficient(var,count)
            objective.SetCoefficient(var,float(cost))
            row.append((tuple(fleet),var))
        options.append(row)
    plans=[]
    for _ in range(solutions):
        if model.Solve() not in (model.OPTIMAL,model.FEASIBLE):break
        selected=[next((fleet,var) for fleet,var in row if var.solution_value()>.5) for row in options]
        ids=defaultdict(list)
        for vehicle,cap in enumerate(capacities):ids[cap].append(vehicle)
        available={cap:iter(vehicles) for cap,vehicles in ids.items()}
        allocation=[tuple(next(available[cap]) for cap in fleet) for fleet,_ in selected]
        plans.append(dict(allocation=allocation,predicted_cost=objective.Value()))
        exclude=model.Constraint(-model.infinity(),len(columns)-1)
        for _,var in selected:exclude.SetCoefficient(var,1)
    return plans


def measure_ordered_columns(path, allocations, *, samples, shortlist, workers, seconds):
    """Screen alternative orders per depot, then measure them and all original columns."""
    from fleet_order_search import candidate_orders
    from score_optimization import optimize as engine

    instance = engine.read_instance(path)
    periods, capacities, locations, history = instance
    original = [set() for _ in locations]
    alternatives = [set() for _ in locations]
    for groups in allocations:
        for depot, ids in enumerate(groups):
            original[depot].add(tuple(capacities[v] for v in ids))
            alternatives[depot].update(tuple(capacities[v] for v in order)
                                       for order in candidate_orders(ids, capacities, samples,
                                                                     seed=17+31*depot))
    screening_budget = min(0.12, seconds / 2)
    with ProcessPoolExecutor(workers, initializer=engine.initialize,
                             initargs=(periods, locations, history)) as executor:
        evaluator = engine.Evaluator(instance, executor)
        pending = [fleets - saved for fleets, saved in zip(alternatives, original)]
        evaluator.costs(((depot, fleet) for depot, fleets in enumerate(pending)
                         for fleet in fleets), screening_budget)
        for depot, fleets in enumerate(pending):
            ranked = sorted(fleets, key=lambda fleet: (evaluator.cached_cost(depot, fleet, screening_budget),
                                                       fleet))
            original[depot].update(ranked[:shortlist])
        evaluator.costs(((depot, fleet) for depot, fleets in enumerate(original)
                         for fleet in fleets), seconds)
        return [{fleet: evaluator.cached_cost(depot, fleet, seconds) for fleet in sorted(fleets)}
                for depot, fleets in enumerate(original)]


def main():
    import argparse
    from pathlib import Path
    from solve import read_instance, read_allocation
    from score_search import ScoreEvaluator
    from score_optimization.optimize import write_allocation
    from result_files import atomic_json

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--reference',type=Path,action='append',required=True)
    parser.add_argument('--outdir',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=3.0)
    parser.add_argument('--solutions',type=int,default=3)
    parser.add_argument('--orders', type=int, default=0,
                        help='Sample this many legal orderings per original fleet before recombining')
    parser.add_argument('--shortlist', type=int, default=6,
                        help='Maximum additional ordered fleets kept per depot after screening')
    parser.add_argument('--workers', type=int, default=3,
                        help='Parallel evaluation workers for the optional order-screening stage')
    args=parser.parse_args()
    if args.seconds<=0 or args.solutions<1 or args.orders<0 or args.shortlist<1 or args.workers<1:
        parser.error('Use nonnegative order counts and positive limits.')
    data=read_instance(args.input)
    allocations = []
    for folder in args.reference:
        file=folder/(args.input.stem+'.out')
        if not file.is_file():continue
        allocations.append(read_allocation(file, data))
        print(f'Loaded candidate: {folder}', flush=True)
    if not allocations:parser.error('No matching reference allocations found.')
    if args.orders:
        columns = measure_ordered_columns(args.input, allocations, samples=args.orders,
                                          shortlist=args.shortlist, workers=args.workers,
                                          seconds=args.seconds)
    else:
        evaluator = ScoreEvaluator(data)
        columns = [{} for _ in range(data['D'])]
        for groups in allocations:
            for depot, ids in enumerate(groups):
                fleet = tuple(data['caps'][v] for v in ids)
                columns[depot][fleet] = evaluator.cost(depot, fleet, args.seconds)
    print(f'Measured fleet counts by depot: {[len(fleets) for fleets in columns]}', flush=True)
    plans=combine_fleets(data['caps'],columns,solutions=args.solutions)
    if not plans:raise RuntimeError('No feasible combination of measured fleets.')
    for rank,plan in enumerate(plans,1):
        write_allocation(args.outdir/f'rank_{rank}'/(args.input.stem+'.out'),plan['allocation'])
    write_allocation(args.outdir/(args.input.stem+'.out'),plans[0]['allocation'])
    atomic_json(args.outdir/(args.input.stem+'.json'),dict(
        method='measured_ordered_fleet_inventory_mip',candidates=plans,
        order_samples=args.orders, ordered_fleets_per_depot=[len(fleets) for fleets in columns],
        note='Predicted costs are sums of search measurements; validate independently before publication.'))


if __name__=='__main__':
    main()
