"""Regression tests for scoring, publication, runtime limits and unseen inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import solve
from compare_algorithms import GraderEvaluator, mutate
from random import Random
from score_search import ScoreEvaluator
from validation import validate_candidates
from runtime_control import RuntimeBudget, BudgetExpired
from publish_results import publish

TEST_WORK = solve.HERE/'_test_work'


class Improvements(unittest.TestCase):
    def setUp(self):
        TEST_WORK.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_WORK)
        self.work = Path(self.temporary.name)
        self.assertTrue(self.work.resolve().is_relative_to(TEST_WORK.resolve()))
        self.input = self.work/'unseen_single.in'
        self.input.write_text('1 2 2\n2 3\n2\n0 0\n1 0 1 1\n2 0 1 1\n')

    def tearDown(self):
        self.temporary.cleanup()

    def test_single_depot_mutation_is_safe(self):
        original = [0, 0]
        result = mutate(original, 1, Random(0))
        self.assertEqual(result, original)
        self.assertIsNot(result, original)

    def test_single_depot_comparison_cli(self):
        result = subprocess.run([sys.executable, str(solve.HERE/'compare_algorithms.py'),
                                 '--file', str(self.input), '--portfolio',
                                 '--output-dir', str(self.work/'comparison')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        groups = solve.read_allocation(self.work/'comparison/unseen_single_single_depot.out',
                                      solve.read_instance(self.input))
        self.assertEqual(groups, [[0, 1]])

    def test_both_evaluators_use_same_scaled_units(self):
        data = dict(caps=[2], T=2, coords=[np.array([[1, 1]])],
                    depots=[np.array([0, 0])], hist=[np.array([[1, 1]])])
        first = ScoreEvaluator(data).score([[0]], 0.02)
        second = GraderEvaluator(data).score([[0]], 0.02)
        self.assertEqual(first, 2.828)
        self.assertEqual(first, second)

    def test_fresh_validation_rejects_a_lucky_outlier(self):
        class FakeEvaluator:
            created = 0

            def __init__(self, data, seed=0):
                self.trial = FakeEvaluator.created
                FakeEvaluator.created += 1
                self.profile = {}

            def score(self, groups, seconds, output_order=False):
                return 100 if groups[0] == [0] else [1, 101, 101][self.trial]

        data = dict(D=2, V=2)
        with patch('score_search.ScoreEvaluator', FakeEvaluator):
            report = validate_candidates(data, {'incumbent': [[0], [1]],
                                                'lucky': [[1], [0]]}, trials=3)
        self.assertEqual(FakeEvaluator.created, 3)
        self.assertEqual(report['selected'], 'incumbent')
        self.assertEqual(report['candidates'][1]['scores'], [1, 101, 101])

    def test_invalid_allocations_raise_value_error(self):
        for groups in ([[0], [0]], [[0], []], [[0], [2]], [[0, 1]]):
            with self.assertRaises(ValueError):
                solve.check_allocation(dict(D=2, V=2), groups)

    def test_empty_customer_depot_and_zero_demand(self):
        data = dict(caps=[2, 3], T=2, coords=[np.empty((0, 2)), np.array([[11, 0]])],
                    depots=[np.array([0, 0]), np.array([10, 0])],
                    hist=[np.empty((0, 2)), np.array([[0, 0]])])
        self.assertEqual(ScoreEvaluator(data).score([[0], [1]], 0.02), 2)

    def test_publication_manifest_matches_output_and_frontend(self):
        out = self.work/'released'
        web = self.work/'published.js'
        manifest = publish([self.input], out, [], trials=1, seconds=0.02, web_data=web)
        self.assertTrue(manifest['complete'])
        report = json.loads((out/'unseen_single.json').read_text(encoding='utf-8'))
        records = json.loads(web.read_text(encoding='utf-8').split('=', 1)[1].strip().rstrip(';'))
        groups = solve.read_allocation(out/'unseen_single.out', solve.read_instance(self.input))
        self.assertEqual(groups, report['allocation'])
        self.assertEqual(groups, records['unseen_single']['allocation'])
        self.assertEqual(report['score'], records['unseen_single']['score'])
        digest = hashlib.sha256((out/'unseen_single.out').read_bytes()).hexdigest()
        self.assertEqual(digest, manifest['cases'][0]['output_sha256'])
        from verify_release import verify
        self.assertEqual(verify(out, self.work, web), 1)
        # Detect a modified webpage instead of merely rechecking generated values.
        records['unseen_single']['allocation'][0].reverse()
        web.write_text('const publishedResults = '+json.dumps(records)+';', encoding='utf-8')
        with self.assertRaises(ValueError):
            verify(out, self.work, web)

    def test_budget_timeout_preserves_feasible_unvalidated_output(self):
        out = self.work/'timeout'
        result = publish([self.input], out, [], time_budget=0.05)
        self.assertFalse(result['complete'])
        self.assertEqual(result['cases'][0]['status'], 'budget_exhausted')
        self.assertIsNone(result['cases'][0]['score'])
        solve.read_allocation(out/'unseen_single.out', solve.read_instance(self.input))

    def test_native_work_can_be_stopped(self):
        started = time.monotonic()
        with self.assertRaises(BudgetExpired):
            RuntimeBudget(0.1).run(time.sleep, 10)
        self.assertLess(time.monotonic() - started, 5)

    def test_default_cli_writes_validated_score_report(self):
        out = self.work/'score_cli'
        result = subprocess.run([sys.executable, str(solve.HERE/'solve.py'), '--file', str(self.input),
                                 '--output-dir', str(out), '--score-trials', '1', '--rounds', '0'],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((out/'unseen_single.json').read_text(encoding='utf-8'))
        self.assertEqual(report['objective'], 'getscore')
        self.assertEqual(report['validation']['status'], 'validated')
        self.assertEqual(report['score'], 4)

    def test_cli_budget_expiry_is_not_reported_as_success(self):
        out = self.work/'budget_cli'
        result = subprocess.run([sys.executable, str(solve.HERE/'solve.py'), '--file', str(self.input),
                                 '--output-dir', str(out), '--time-budget', '0.05'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads((out/'unseen_single.json').read_text(encoding='utf-8'))
        self.assertIsNone(report['score'])
        self.assertEqual(report['validation']['status'], 'budget_exhausted')
        solve.read_allocation(out/'unseen_single.out', solve.read_instance(self.input))

    def test_partial_publication_keeps_other_verified_cases(self):
        second = self.work/'another_case.in'
        second.write_bytes(self.input.read_bytes())
        out, web = self.work/'partial', self.work/'partial.js'
        publish([self.input, second], out, [], trials=1, seconds=0.02, web_data=web)
        before = (out/'another_case.out').read_bytes()
        manifest = publish([self.input], out, [], trials=1, seconds=0.02, web_data=web)
        self.assertEqual(len(manifest['cases']), 2)
        self.assertEqual(manifest['retained_cases'], ['another_case'])
        self.assertEqual((out/'another_case.out').read_bytes(), before)
        from verify_release import verify
        self.assertEqual(verify(out, self.work, web), 2)

    def test_output_order_matches_the_supplied_grader(self):
        data = dict(caps=[2, 3], T=2, coords=[np.array([[1, 0]])],
                    depots=[np.array([0, 0])], hist=[np.array([[1, 1]])])
        evaluator = ScoreEvaluator(data)
        with patch.object(evaluator, 'cost', return_value=0) as route_cost:
            evaluator.score([[1, 0]], 0.02, output_order=True)
        route_cost.assert_called_once_with(0, (3, 2), 0.02)


if __name__ == '__main__':
    unittest.main()
