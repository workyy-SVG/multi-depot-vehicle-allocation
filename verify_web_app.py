"""Local Streamlit tests; no real API key or external model request is used."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent


class WebAppTests(unittest.TestCase):
    def test_scoring_table_uses_the_published_allocation(self):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py'), default_timeout=20).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox[0].value, 'getscore')
        app.selectbox[1].select('08_random').run()
        self.assertEqual(len(app.exception), 0)
        report = json.loads((ROOT/'编程作业1/released/08_random.json').read_text(encoding='utf-8'))
        actual = list(app.table[0].value['车辆数'])
        self.assertEqual(actual, [len(group) for group in report['allocation']])
        self.assertEqual(len(app.table[1].value), len(report['validation']['candidates']))

    def test_historical_window_selection_still_works(self):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py'), default_timeout=20)
        app.query_params['mode'] = 'historical'
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox[0].value, 'historical')
        app.selectbox[2].select(1).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.selectbox[2].value, 1)

    def test_linked_question_is_prefilled_without_sending(self):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py'), default_timeout=20)
        app.query_params['mode'] = 'getscore'
        app.query_params['question'] = '请解释当前正式提交方案。'
        app.secrets['DEEPSEEK_API_KEY'] = 'local-test-placeholder'
        with patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected external request')) as network:
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.text_area[0].value, '请解释当前正式提交方案。')
        network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
