"""Independent small known-answer checks plus output conservation checks."""
import importlib.util
import unittest
import numpy as np
from solve import read_instance, distances, route_cost, route_pool, check_allocation, HERE
from refine_eval import Depot, Scenario
from fleet_search import improve_fleet


class Checks(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("pyvrp"), "optional PyVRP dependency")
    def test_getscore_uses_fractional_mean_and_round_trip_penalty(self):
        from score_search import ScoreEvaluator

        evaluator = ScoreEvaluator({
            'caps': [2, 3], 'T': 2,
            'coords': [np.array([[1, 0], [2, 0]])],
            'depots': [np.array([0, 0])],
            'hist': [np.array([[1, 2], [2, 1]])],
        })
        self.assertAlmostEqual(evaluator.score([[]], 0.1), 6)
        self.assertAlmostEqual(evaluator.score([[0]], 0.1), 6)
        self.assertAlmostEqual(evaluator.score([[1]], 0.1), 4)

    @unittest.skipUnless(importlib.util.find_spec("pyvrp") and importlib.util.find_spec("ortools"),
                         "optional PyVRP and OR-Tools dependencies")
    def test_score_polish_can_recombine_three_depots(self):
        from score_search import recombine_fleets

        class Evaluator:
            capacities = [1, 2, 3, 4, 5, 6]
            depots = range(3)
            targets = ((1, 4), (3, 6), (2, 5))

            def fleet(self, ids):
                return tuple(sorted(self.capacities[vehicle] for vehicle in ids))

            def cost(self, depot, fleet, seconds):
                return len(set(fleet).symmetric_difference(self.targets[depot]))

        evaluator = Evaluator()
        solutions = recombine_fleets(evaluator, [[[0, 1], [2, 3], [4, 5]]], 0.01)
        self.assertEqual(tuple(map(evaluator.fleet, solutions[0])), evaluator.targets)
        for allocation in solutions:
            check_allocation({'D': 3, 'V': 6}, allocation)

    @unittest.skipUnless(importlib.util.find_spec("ortools"), "optional OR-Tools dependency")
    def test_route_objective(self):
        # Depot 0, customers at 1 and 2 on a line. One truck of capacity 2
        # visits both for cost 4, strictly better than dropping both (cost 12).
        mat = distances(np.array([[1.,0.],[2.,0.]]),np.array([0.,0.]))
        self.assertAlmostEqual(route_cost(mat,np.array([1,1]),[2],200),4.)
        self.assertAlmostEqual(route_cost(mat,np.array([1,1]),[],100),12.)
        self.assertAlmostEqual(route_cost(mat,np.array([3,3]),[2],100),12.)
        self.assertAlmostEqual(route_cost(mat,np.array([0,0]),[2],100),0.)

    def test_unserved_customer_is_charged(self):
        scenario = Scenario(Depot([[100, 0], [1, 0], [2, 0]]), [3, 1, 1])
        routes, dropped = scenario.greedy([1, 2, 3], [2])
        self.assertEqual(dropped, [1])
        self.assertAlmostEqual(scenario.raw_cost(routes, dropped), 404)
        self.assertAlmostEqual(scenario.eval([2]), 404)
        self.assertAlmostEqual(scenario.eval([]), 412)

    def test_route_pool_includes_single_customer_savings(self):
        coords = np.array([[1., 0.], [2., 0.]])
        depot = np.zeros(2)
        data = {'D': 1, 'V': 1, 'caps': [2], 'coords': [coords], 'depots': [depot]}
        pool = route_pool(data, [np.array([[1], [1]])], [distances(coords, depot)], 1)
        singles = {tuple(route): saving for route, _, saving in pool[0, 0] if len(route) == 1}
        self.assertEqual(singles, {(1,): 2., (2,): 4.})

    def test_fleet_search_can_move_and_swap(self):
        class Evaluator:
            data = {'caps': [1, 2, 3]}
            targets = ((2,), (1, 3))

            def capacities(self, vehicles):
                return tuple(sorted(self.data['caps'][v] for v in vehicles))

            def cost(self, depot, fleet, periods):
                return sum(abs(fleet.count(v) - self.targets[depot].count(v))
                           for v in self.data['caps'])

            def score(self, allocation, periods):
                return sum(self.cost(d, self.capacities(ids), periods)
                           for d, ids in enumerate(allocation))

        evaluator = Evaluator()
        for initial in ([[1, 2], [0]], [[0], [1, 2]]):
            _, final = improve_fleet(evaluator, {'start': initial}, [0], 1)
            self.assertEqual(evaluator.score(final, [0]), 0)
            check_allocation({'D': 2, 'V': 3}, final)

    def test_heterogeneous_matching_rule(self):
        # Enumerate all small route-load/fleet pairs and compare nested
        # threshold constraints with brute-force injective matching.
        import itertools
        types = [1,2,3]
        for n in range(1,4):
            for fleet in itertools.combinations_with_replacement(types,n):
                for m in range(n+2):
                    for loads in itertools.combinations_with_replacement(types,m):
                        nested = all(sum(l>=c for l in loads)<=sum(v>=c for v in fleet) for c in types)
                        brute = any(all(l<=v for l,v in zip(loads,p)) for p in itertools.permutations(fleet,m)) if m<=n else False
                        self.assertEqual(nested,brute,(fleet,loads))

    def test_outputs(self):
        files = sorted((HERE/'input_data').glob('*.in'))
        self.assertEqual(len(files),9)
        for path in files:
            data = read_instance(path); out=HERE/'output'/(path.stem+'.out')
            self.assertTrue(out.exists(),str(out))
            lines=out.read_text().splitlines(); self.assertEqual(len(lines),data['D'])
            allocation=[]
            for line in lines:
                row=list(map(int,line.split())); self.assertEqual(row[0],len(row)-1); allocation.append(row[1:])
            check_allocation(data,allocation)

    def test_rolling_validation_has_no_future_leakage(self):
        import json

        for path in sorted((HERE / 'input_data').glob('*.in')):
            data = read_instance(path)
            record = json.loads((HERE / 'output' / (path.stem + '.json')).read_text(encoding='utf8'))
            rows = (HERE / 'output' / (path.stem + '.out')).read_text().splitlines()
            self.assertEqual([list(map(int, row.split()))[1:] for row in rows],
                             [sorted(group) for group in record['allocation']])
            self.assertEqual(record['penalty_multiplier'], 4)
            self.assertEqual(len(record['rolling_validation']), 2)
            for window in record['rolling_validation']:
                split = window['train_end_exclusive']
                self.assertLess(max(window['search_periods']), split)
                self.assertLessEqual(split, min(window['test_periods']))
                self.assertLess(max(window['test_periods']), data['T'])
            self.assertLessEqual(record['search_costs']['local_search'],
                                 record['search_costs'][record['seed']] + 1e-8)


if __name__ == '__main__': unittest.main()
