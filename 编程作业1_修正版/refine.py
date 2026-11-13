"""Read-only re-evaluation of generated candidate allocations."""
from __future__ import annotations
import argparse, json, pathlib, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
if (HERE / '_deps').exists():
    sys.path.insert(0, str(HERE / '_deps'))
import solve
import refine_eval as ce


class DepotEval:
    def __init__(self, xy, hist, periods):
        self.depot = ce.Depot(xy)
        self.hist = hist
        self.periods = list(periods)
        self.scn = {}
        self.cache = {}

    def cost(self, caps, t, effort):
        key = (int(t), tuple(sorted(int(c) for c in caps if c > 0)))
        if key not in self.cache:
            scn = self.scn.get(key[0])
            if scn is None:
                scn = ce.Scenario(self.depot, self.hist[:, int(t)],
                                  order_rot=8, cut_rot=3)
                self.scn[key[0]] = scn
            if effort >= 2:
                self.cache[key] = scn.eval(caps, refine_top=6, dp_top=3)
            else:
                self.cache[key] = scn.eval(caps, rotations=12, refine_top=3, dp_top=2)
        return self.cache[key]

    def expected(self, caps, effort):
        return float(np.mean([self.cost(caps, t, effort) for t in self.periods]))


def evaluate(data, allocations, periods, effort):
    deps = [DepotEval(data['coords'][d] - data['depots'][d],
                      data['hist'][d], periods) for d in range(data['D'])]
    result = {}
    for name, alloc in allocations.items():
        total = 0.0
        for d, ids in enumerate(alloc):
            caps = [data['caps'][v] for v in ids]
            total += deps[d].expected(caps, effort)
        result[name] = total
    return result


def choose_periods(data, rep, args):
    if args.periods:
        return [int(x) for x in args.periods.split(',')]
    if args.all_periods:
        return list(range(data['T']))
    return rep.get('search_periods') or list(range(data['T']))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir', default=str(HERE / 'input_data'))
    p.add_argument('--output-dir', default=str(HERE / 'output'))
    p.add_argument('--file', help='single .in file')
    p.add_argument('--periods', help='comma-separated, zero-based period indices')
    p.add_argument('--all-periods', action='store_true')
    p.add_argument('--effort', type=int, default=1, choices=(1, 2))
    args = p.parse_args()
    files = ([pathlib.Path(args.file)] if args.file
             else sorted(pathlib.Path(args.input_dir).glob('*.in')))
    outdir = pathlib.Path(args.output_dir)
    for path in files:
        data = solve.read_instance(path)
        rep = json.loads((outdir / (path.stem + '.json')).read_text(encoding='utf-8'))
        allocations = dict(rep['candidates'])
        if 'deterministic_refined' in rep:
            allocations['deterministic_refined'] = rep['deterministic_refined']
        periods = choose_periods(data, rep, args)
        scores = evaluate(data, allocations, periods, args.effort)
        base = min(scores.values())
        print('%s  periods=%d  effort=%d' % (path.name, len(periods), args.effort))
        for name, score in sorted(scores.items(), key=lambda z: z[1]):
            print('  %-24s %12.2f  %+7.2f%%'
                  % (name, score, 100 * (score - base) / base if base else 0))
        print('  stored output: %s' % rep['selected'])


if __name__ == '__main__':
    main()
