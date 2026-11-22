"""Atomic score reports consumed by the publisher, webpage, and Streamlit."""
import hashlib
import json
from importlib.metadata import version
from pathlib import Path


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def score_report(input_path, data, allocation, *, validation=None, source='',
                 status='budget_exhausted'):
    validation = validation or dict(status=status, trials=0, candidates=[])
    selected = validation.get('selected')
    row = next((r for r in validation.get('candidates', []) if r['name'] == selected), None)
    return dict(input=Path(input_path).name,
                input_sha256=hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),
                objective='getscore', penalty_multiplier=2, distance_scale=1000,
                allocation=[list(group) for group in allocation],
                capacities=[sum(data['caps'][v] for v in group) for group in allocation],
                declared_customer_counts=data['declared_counts'],
                ignored_trailing_records=data['trailing_records'], periods=data['T'],
                selected_source=source or selected, score=row['median'] if row else None,
                validation=validation,
                environment=dict(pyvrp=version('pyvrp'), numpy=version('numpy')))
