"""Recombine measured ordered fleets by capacity inventory, independent of IDs."""
from collections import Counter, defaultdict
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
    args=parser.parse_args()
    if args.seconds<=0 or args.solutions<1:parser.error('Positive runtime and solution count are required.')
    data=read_instance(args.input)
    evaluator=ScoreEvaluator(data)
    columns=[{} for _ in range(data['D'])]
    found=False
    for folder in args.reference:
        file=folder/(args.input.stem+'.out')
        if not file.is_file():continue
        groups=read_allocation(file,data);found=True
        for depot,ids in enumerate(groups):
            fleet=tuple(data['caps'][v] for v in ids)
            columns[depot][fleet]=evaluator.cost(depot,fleet,args.seconds)
        print(f'Measured candidate: {folder}',flush=True)
    if not found:parser.error('No matching reference allocations found.')
    plans=combine_fleets(data['caps'],columns,solutions=args.solutions)
    if not plans:raise RuntimeError('No feasible combination of measured fleets.')
    for rank,plan in enumerate(plans,1):
        write_allocation(args.outdir/f'rank_{rank}'/(args.input.stem+'.out'),plan['allocation'])
    write_allocation(args.outdir/(args.input.stem+'.out'),plans[0]['allocation'])
    atomic_json(args.outdir/(args.input.stem+'.json'),dict(
        method='measured_ordered_fleet_inventory_mip',candidates=plans,
        note='Predicted costs are sums of search measurements; validate independently before publication.'))


if __name__=='__main__':
    main()
