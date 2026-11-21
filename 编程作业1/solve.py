"""Multi-period heterogeneous depot allocation. Python 3.10+, numpy, ortools.

Run: python solve.py --input-dir input_data --output-dir output
The CP-SAT optimum is over a restricted route pool, NOT the full CVRP.
"""
from __future__ import annotations
import argparse, collections, csv, json, math, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent
if (HERE / '_deps').exists():
    sys.path.insert(0, str(HERE / '_deps'))
import numpy as np
from refine_eval import UNSERVED_DISTANCE_MULTIPLIER
from fleet_search import FleetEvaluator, improve_fleet


def read_instance(path):
    lines = pathlib.Path(path).read_text(encoding='utf-8-sig').splitlines()
    d, v, t = map(int, lines[0].split())
    caps = list(map(int, lines[1].split()))
    counts = list(map(int, lines[2].split()))
    assert len(caps) == v and len(counts) == d and min(caps) > 0
    depots = np.array([list(map(float, x.split())) for x in lines[3:3+d]])
    coords, hist, pos = [], [], 3+d
    for n in counts:
        rows = np.array([list(map(float, x.split())) for x in lines[pos:pos+n]]).reshape(n, t+2)
        assert rows.shape == (n, t+2)
        coords.append(rows[:, :2]); hist.append(rows[:, 2:]); pos += n
    assert pos <= len(lines) and all((h >= 0).all() for h in hist)
    extra = len(lines)-pos
    if extra:
        print(f'INPUT NOTICE: {pathlib.Path(path).name}: {extra} trailing records beyond declared customer counts; using header counts.',flush=True)
    return dict(D=d, V=v, T=t, caps=caps, depots=depots, coords=coords, hist=hist,
                trailing_records=extra, declared_counts=counts)


def distances(xy, depot):
    points = np.vstack([depot, xy])
    return np.sqrt(((points[:, None] - points[None, :]) ** 2).sum(axis=2))


def baseline(data, hist, kind):
    means = np.array([h.sum(axis=0).mean() for h in hist])
    if kind == 'balanced':
        weights = np.ones(data['D'])
    elif kind == 'mean':
        weights = means
    else:
        weights = means + np.array([h.sum(axis=0).std() for h in hist])
    weights = np.maximum(weights, 1e-9); weights /= weights.sum()
    load = np.zeros(data['D']); allocation = [[] for _ in weights]
    total = sum(data['caps'])
    for v in sorted(range(data['V']), key=lambda v: (-data['caps'][v], v)):
        # Greedy minimizes squared deviations from target depot capacity.
        c = data['caps'][v]
        gain = (load+c-total*weights)**2 - (load-total*weights)**2
        d = int(np.argmin(gain)); allocation[d].append(v); load[d] += c
    return allocation


def scenarios(hist, count):
    """Synchronized period samples; trend adjustment only if clearly increasing."""
    t = hist[0].shape[1]
    ids = np.linspace(0, t-1, min(t, count), dtype=int)
    answer, trends = [], []
    for h in hist:
        total = h.sum(axis=0); x = np.arange(t, dtype=float)
        slope, intercept = np.polyfit(x, total, 1) if t > 2 else (0., total.mean())
        fitted = intercept+slope*x
        noise = float(np.std(total-fitted))
        increasing = slope > 0 and slope*(t-1) > max(0.15*total.mean(), 2*noise)
        if increasing:
            target = max(0., intercept+slope*t)
            ratios = target/np.maximum(fitted[ids], 1.)
            values = np.rint(h[:, ids]*np.clip(ratios, 0.5, 2.0)).astype(int)
        else:
            values = np.rint(h[:, ids]).astype(int)
        answer.append(values); trends.append(bool(increasing))
    return answer, trends


def improve_order(route, dist):
    """Nearest-neighbour and bounded 2-opt, retaining the input if better."""
    if len(route) <= 2:
        return route
    def length(r):
        return sum(dist[a, b] for a, b in zip([0]+r, r+[0]))
    pending = list(route); nn = []; last = 0
    while pending:
        k = int(np.argmin(dist[last, pending])); last = pending.pop(k); nn.append(last)
    best = nn if length(nn) < length(route) else list(route)
    if len(best) <= 100:
        r = [0]+best+[0]
        for _ in range(2):
            changed = False
            for i in range(1, len(r)-2):
                for j in range(i+1, len(r)-1):
                    a,b,c,d = r[i-1],r[i],r[j],r[j+1]
                    if dist[a,c]+dist[b,d] < dist[a,b]+dist[c,d]-1e-9:
                        r[i:j+1] = reversed(r[i:j+1]); changed = True
            if not changed: break
        best = r[1:-1]
    return best


def route_pool(data, scen, mats, offsets=4):
    pools = {}; types = sorted(set(data['caps']))
    for d in range(data['D']):
        xy = data['coords'][d]-data['depots'][d]
        angle = np.arctan2(xy[:,1], xy[:,0])
        for s in range(scen[d].shape[1]):
            q = scen[d][:, s]; active = np.flatnonzero(q > 0)
            angular = active[np.argsort(angle[active])].tolist()
            found = {}
            def add_route(r, amount):
                if not r: return
                key = tuple(sorted(r))
                if key in found: return
                r = improve_order(r, mats[d])
                cost = sum(mats[d][a,b] for a,b in zip([0]+r,r+[0]))
                saving = sum(UNSERVED_DISTANCE_MULTIPLIER*mats[d][0,i] for i in r)-cost
                if saving > 1e-8:
                    level = next(i for i,c in enumerate(types) if c >= amount)
                    found[key] = (r, level, float(saving))
            for i in active:
                demand = int(q[i])
                if demand <= types[-1]:
                    add_route([int(i) + 1], demand)
            # Even a large truck may drive a short, partly loaded route.
            # Smaller packing targets are essential when fleet capacity is abundant.
            targets = set(types)
            targets.update(max(1,types[0]//f) for f in (2,4,8,16))
            targets.update(max(1,int(q.sum()/n)) for n in (2,4,8,data['V']))
            for cap in sorted(c for c in targets if c <= types[-1]):
                for flip in (False, True):
                    seq = angular[::-1] if flip else angular
                    for off in range(offsets):
                        cut = len(seq)*off//offsets
                        order = seq[cut:]+seq[:cut]; route = []; load = 0
                        for i in order:
                            demand = int(q[i])
                            if demand > cap: continue
                            if load+demand > cap:
                                add_route(route, load); route = []; load = 0
                            route.append(i+1); load += demand
                        add_route(route, load)
            pools[d,s] = list(found.values())
    return pools


def optimize_pool(data, scen, mats, seconds, offsets):
    from ortools.sat.python import cp_model

    start = time.perf_counter(); pools = route_pool(data, scen, mats, offsets)
    model = cp_model.CpModel(); types = sorted(set(data['caps']))
    inventory = collections.Counter(data['caps']); k = len(types); D = data['D']
    x = [[model.new_int_var(0, inventory[c], f'x{d}_{i}') for i,c in enumerate(types)] for d in range(D)]
    for i,c in enumerate(types): model.add(sum(x[d][i] for d in range(D)) == inventory[c])
    terms = []; route_count = 0
    for (d,s), routes in pools.items():
        covering = collections.defaultdict(list); levels = [[] for _ in types]
        for j,(nodes, level, saving) in enumerate(routes):
            y = model.new_bool_var(f'r{d}_{s}_{j}'); route_count += 1
            levels[level].append(y)
            for node in nodes: covering[node].append(y)
            terms.append(int(round(saving*1000))*y)
        for ys in covering.values(): model.add(sum(ys) <= 1)
        # Nested cardinality constraints are sufficient for matching routes to
        # heterogeneous vehicles (sort route loads and available capacities).
        for i in range(k):
            model.add(sum(y for group in levels[i:] for y in group) <= sum(x[d][i:]))
    model.maximize(sum(terms))
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1; solver.parameters.random_seed = 20261110
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, dict(status=solver.status_name(status), routes=route_count)
    remaining = {c:[i for i,a in enumerate(data['caps']) if a == c] for c in types}
    allocation = [[] for _ in range(D)]
    for d in range(D):
        for i,c in enumerate(types):
            for _ in range(solver.value(x[d][i])): allocation[d].append(remaining[c].pop(0))
    return allocation, dict(status=solver.status_name(status), routes=route_count,
        pool_saving=solver.objective_value/1000,
        pool_saving_bound=solver.best_objective_bound/1000,
        seconds=time.perf_counter()-start)


def route_cost(mat, demand, caps, milliseconds=200):
    """Evaluate CVRP routes with the assignment penalty for optional visits."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    active = np.flatnonzero(demand > 0)+1
    if len(active) == 0: return 0.
    penalty = float(UNSERVED_DISTANCE_MULTIPLIER*mat[0,active].sum())
    if not caps: return penalty
    # Always keep a constructive feasible solution, including when OR-Tools
    # reaches its time limit before finding an initial solution on large data.
    fallback = penalty
    for far_first in (False, True):
        pending = set(int(i) for i in active); travel = 0.
        for cap in sorted(caps,reverse=True):
            route = []; left = cap; last = 0
            while True:
                feasible = [i for i in pending if demand[i-1] <= left]
                if not feasible: break
                if last == 0 and far_first:
                    node = max(feasible,key=lambda i: (mat[0,i],-i))
                else:
                    node = min(feasible,key=lambda i: (mat[last,i],i))
                route.append(node); left -= int(demand[node-1]); pending.remove(node); last = node
            route = improve_order(route,mat)
            travel += sum(mat[a,b] for a,b in zip([0]+route,route+[0]))
        travel += sum(UNSERVED_DISTANCE_MULTIPLIER*mat[0,i] for i in pending)
        fallback = min(fallback,float(travel))
    nodes = np.r_[0,active]; dm = np.r_[0, np.rint(demand[active-1]).astype(int)]
    dm = [int(i) for i in dm]
    small = mat[np.ix_(nodes,nodes)]
    matrix = np.rint(small*1000).astype(np.int64).tolist()
    manager = pywrapcp.RoutingIndexManager(len(nodes), len(caps), 0)
    routing = pywrapcp.RoutingModel(manager)
    cb = routing.RegisterTransitCallback(lambda a,b: matrix[manager.IndexToNode(a)][manager.IndexToNode(b)])
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    db = routing.RegisterUnaryTransitCallback(lambda a: dm[manager.IndexToNode(a)])
    routing.AddDimensionWithVehicleCapacity(db, 0, sorted(caps), True, 'Capacity')
    for i in range(1,len(nodes)):
        routing.AddDisjunction([manager.NodeToIndex(i)], int(round(UNSERVED_DISTANCE_MULTIPLIER*small[0,i]*1000)))
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(milliseconds)
    sol = routing.SolveWithParameters(params)
    if sol is None: return fallback
    seen = set(); cost = 0.
    for v in range(len(caps)):
        idx = routing.Start(v); load = 0
        while not routing.IsEnd(idx):
            ni = manager.IndexToNode(idx); nxt = sol.Value(routing.NextVar(idx)); nj = manager.IndexToNode(nxt)
            cost += float(small[ni,nj])
            if ni:
                assert ni not in seen; seen.add(ni); load += dm[ni]
            idx = nxt
        assert load <= sorted(caps)[v]
    cost += sum(UNSERVED_DISTANCE_MULTIPLIER*float(small[0,i]) for i in range(1,len(nodes)) if i not in seen)
    return min(cost, fallback)


def evaluate(data, hist, allocations, mats, periods, milliseconds):
    cache = {}; scores = {}
    for name, alloc in allocations.items():
        costs = []
        for t in periods:
            total = 0.
            for d, ids in enumerate(alloc):
                caps = tuple(sorted(data['caps'][v] for v in ids)); key = (d,t,caps)
                if key not in cache:
                    cache[key] = route_cost(mats[d],hist[d][:,t],caps,milliseconds)
                total += cache[key]
            costs.append(total)
        scores[name] = float(np.mean(costs))
    return scores


def check_allocation(data, allocation):
    assert len(allocation) == data['D']
    assert sorted(v for group in allocation for v in group) == list(range(data['V']))


def sample_periods(start, stop, count):
    return np.linspace(start, stop - 1, min(stop - start, count), dtype=int).tolist()


def write_allocation(path, allocation):
    path.write_text('\n'.join(' '.join(map(str, [len(ids)] + sorted(ids))) for ids in allocation) + '\n',
                    encoding='utf-8')


def read_allocation(path, data):
    rows = [list(map(int, line.split())) for line in path.read_text().splitlines()]
    if any(not row or row[0] != len(row) - 1 for row in rows):
        raise ValueError(f'Invalid vehicle counts in {path}')
    allocation = [row[1:] for row in rows]
    check_allocation(data, allocation)
    return allocation


def solve_for_score(path, outdir, args):
    from score_search import optimize

    data = read_instance(path)
    seeds = [baseline(data, data['hist'], kind) for kind in ('balanced', 'mean', 'risk')]
    reference_file = pathlib.Path(args.baseline_dir) / (path.stem + '.out')
    reference = read_allocation(reference_file, data) if reference_file.is_file() else seeds[0]
    incumbent = None
    if args.incumbent_dir:
        incumbent_file = pathlib.Path(args.incumbent_dir) / reference_file.name
        if incumbent_file.is_file():
            incumbent = read_allocation(incumbent_file, data)
    final, before, incumbent_score, after = optimize(
        data, [reference, *seeds], reference, args.rounds, args.score_search_ms / 1000,
        incumbent=incumbent, polish=args.score_polish,
    )
    check_allocation(data, final)
    write_allocation(outdir / (path.stem + '.out'), final)
    print(f'{path.name}: baseline {before:.4f}, incumbent {incumbent_score:.4f}, final {after:.4f}',
          flush=True)
    source = 'balanced'
    if reference_file.is_file():
        source = str(reference_file)
        if reference_file.is_relative_to(HERE):
            source = reference_file.relative_to(HERE).as_posix()
    return (path.name, source, before, after, before - after if math.isfinite(before) else None,
            incumbent_score, incumbent_score - after if math.isfinite(incumbent_score) else None)


def solve(path, outdir, args):
    data = read_instance(path)
    evaluator = FleetEvaluator(data)
    total_periods = data['T']
    candidate_names = ('balanced', 'mean', 'risk')
    splits = sorted({int(total_periods * fraction) for fraction in (0.6, 0.8)})
    audits = []
    for start, stop in zip(splits, splits[1:] + [total_periods]):
        if start == 0 or start >= stop:
            continue
        hist = [h[:, :start] for h in data['hist']]
        candidates = {name: baseline(data, hist, name) for name in candidate_names}
        demand_scenarios, _ = scenarios(hist, args.scenarios)
        candidates['trend'] = baseline(data, demand_scenarios, 'mean')
        train = sample_periods(0, start, args.train_periods)
        _, candidates['local_search'] = improve_fleet(evaluator, candidates, train, args.rounds)
        test = sample_periods(start, stop, args.eval_periods)
        scores = {name: evaluator.score(alloc, test) for name, alloc in candidates.items()}
        audits.append(dict(train_end_exclusive=start, search_periods=train,
                           test_periods=test, costs=scores))

    hist = data['hist']
    candidates = {name: baseline(data, hist, name) for name in candidate_names}
    scen, trends = scenarios(hist, args.scenarios)
    candidates['trend'] = baseline(data, scen, 'mean')
    optimizer = dict(status='DISABLED')
    if args.cp_sat:
        mats = [distances(xy, dep) for xy, dep in zip(data['coords'], data['depots'])]
        allocation, optimizer = optimize_pool(data, scen, mats, args.seconds, args.offsets)
        if allocation is not None:
            candidates['scenario_cp_sat'] = allocation
    train = sample_periods(0, total_periods, args.train_periods)
    seed, final = improve_fleet(evaluator, candidates, train, args.rounds)
    check_allocation(data, final)
    candidates['local_search'] = final
    target = outdir / (path.stem + '.out')
    write_allocation(target, final)
    report = dict(input=path.name, selected='local_search', seed=seed,
                  allocation=final, candidates=candidates, rolling_validation=audits,
                  search_periods=train,
                  search_costs={name: evaluator.score(alloc, train) for name, alloc in candidates.items()},
                  declared_customer_counts=data['declared_counts'],
                  ignored_trailing_records=data['trailing_records'],
                  capacities=[sum(data['caps'][v] for v in ids) for ids in final],
                  penalty_multiplier=UNSERVED_DISTANCE_MULTIPLIER, optimizer=optimizer,
                  trend_detected=trends, settings=vars(args))
    (outdir / (path.stem + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding='utf-8')
    print(path.name, seed, 'vehicles=', [len(g) for g in final], flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir',default=str(HERE/'input_data'))
    p.add_argument('--output-dir')
    p.add_argument('--file',help='Optional single .in file')
    p.add_argument('--objective', choices=('historical', 'getscore'), default='historical')
    p.add_argument('--baseline-dir', default='output', help='Reference allocations for getscore')
    p.add_argument('--incumbent-dir', help='Keep stronger allocations from an earlier getscore run')
    p.add_argument('--score-polish', action='store_true',
                   help='Recombine scored fleets and rerank a wider shortlist')
    p.add_argument('--score-search-ms', type=int, default=30,
                   help='Routing milliseconds per depot fleet in the first getscore pass')
    p.add_argument('--cp-sat', action='store_true', help='Add a CP-SAT seed to the final fit')
    p.add_argument('--train-periods', type=int, default=4)
    p.add_argument('--rounds', type=int, help='Fleet search rounds (historical: 2, getscore: 5)')
    p.add_argument('--seconds',type=float,default=35)
    p.add_argument('--scenarios',type=int,default=5)
    p.add_argument('--offsets',type=int,default=3)
    p.add_argument('--eval-periods',type=int,default=3)
    args = p.parse_args()
    if args.rounds is None:
        args.rounds = 5 if args.objective == 'getscore' else 2
    default_output = 'score_output' if args.objective == 'getscore' else 'output'
    out = pathlib.Path(args.output_dir) if args.output_dir else HERE / default_output
    files = [pathlib.Path(args.file)] if args.file else sorted(pathlib.Path(args.input_dir).glob('*.in'))
    if not files: raise SystemExit('No input files found.')
    if min(args.train_periods, args.eval_periods, args.scenarios, args.offsets) < 1 or args.rounds < 0:
        p.error('period counts, scenarios and offsets must be positive; rounds must be nonnegative')
    if args.score_search_ms < 1:
        p.error('--score-search-ms must be positive')
    out.mkdir(parents=True, exist_ok=True)
    if args.objective == 'getscore':
        args.baseline_dir = str(HERE / args.baseline_dir)
        with (out / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['input', 'reference', 'baseline_score', 'optimized_score', 'reduction',
                             'incumbent_score', 'reduction_vs_incumbent'])
            for path in files:
                writer.writerow(solve_for_score(path, out, args))
                f.flush()
        return
    reports = [solve(path, out, args) for path in files]
    with (out / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['input', 'train_end_exclusive', 'test_periods',
                         'balanced', 'mean', 'risk', 'trend', 'local_search'])
        for report in reports:
            for row in report['rolling_validation']:
                writer.writerow([report['input'], row['train_end_exclusive'],
                                 ' '.join(map(str, row['test_periods'])),
                                 *(row['costs'][k] for k in ('balanced', 'mean', 'risk', 'trend', 'local_search'))])


if __name__ == '__main__': main()
