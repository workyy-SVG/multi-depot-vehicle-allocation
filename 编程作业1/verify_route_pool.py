"""Known-answer checks for global route packing and vehicle matching."""
import unittest

from route_pool_search import assign_trucks, pack_routes
from solve import check_allocation
from fleet_order_search import candidate_orders
from measured_fleet_portfolio import combine_fleets


class RoutePoolChecks(unittest.TestCase):
    def test_capacity_stock_is_shared_between_depots(self):
        # One large truck cannot cover both attractive large routes.
        columns = [
            dict(depot=0, nodes=(1, 2), load=1000, saving=100),
            dict(depot=1, nodes=(1, 2), load=1000, saving=99),
            dict(depot=1, nodes=(1,), load=500, saving=50),
            dict(depot=0, nodes=(1,), load=500, saving=60),
        ]
        plans = pack_routes(columns, [5, 10], 100, set(), seconds=2, solutions=1)
        self.assertEqual(sum(row['saving'] for row in plans[0]), 159)
        allocation = assign_trucks(plans[0], [5, 10], 100, 2, ((0,), (1,)), [500, 1000])
        self.assertEqual(allocation, ((0,), (1,)))

    def test_matching_and_unused_vehicles_remain_legal(self):
        route = dict(depot=1, load=1000)
        caps = [3, 5, 10]
        for matching in ('smallest', 'largest', 'random'):
            for spares in ('original', 'balance', '0', '1'):
                groups = assign_trucks([route], caps, 100, 2, ((0, 1), (2,)),
                                       [400, 1000], matching=matching, spares=spares)
                check_allocation(dict(D=2, V=3), groups)
                self.assertIn(2, groups[1])

    def test_impossible_route_is_rejected(self):
        with self.assertRaises(ValueError):
            assign_trucks([dict(depot=0, load=1001)], [10], 100, 1, ((0,),), [1001])

    def test_order_candidates_preserve_vehicle_ownership(self):
        caps = [5, 5, 10]
        orders = candidate_orders((2, 1, 0), caps, count=10, seed=0)
        self.assertEqual(len(orders), 3)
        self.assertEqual(len({tuple(caps[v] for v in ids) for ids in orders}), 3)
        for ids in orders:
            self.assertEqual(sorted(ids), [0, 1, 2])

    def test_measured_fleets_match_type_inventory_and_keep_order(self):
        plans = combine_fleets([5,5,10], [{(5,10):3,(10,5):7},{(5,):1}], solutions=1)
        self.assertEqual(plans[0]['predicted_cost'],4)
        groups=plans[0]['allocation']
        self.assertEqual(tuple([5,5,10][v] for v in groups[0]),(5,10))
        check_allocation(dict(D=2,V=3),groups)


if __name__ == '__main__':
    unittest.main()
