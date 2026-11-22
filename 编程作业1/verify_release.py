"""Verify that the submission manifest, reports and static webpage agree."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(released, inputs, web_data, *, allow_incomplete=False):
    released, inputs, web_data = map(Path, (released, inputs, web_data))
    manifest = json.loads((released/'manifest.json').read_text(encoding='utf-8'))
    content = web_data.read_text(encoding='utf-8')
    prefix = 'const publishedResults = '
    if not content.startswith(prefix):
        raise ValueError('Unexpected frontend data format.')
    frontend = json.loads(content[len(prefix):].strip().rstrip(';'))
    names = [Path(row['input']).stem for row in manifest['cases']]
    if not names or len(set(names)) != len(names) or set(names) != set(frontend):
        raise ValueError('Manifest and frontend instance lists differ.')
    if not allow_incomplete and not manifest['complete']:
        raise ValueError('Release includes unvalidated fallbacks.')
    for row in manifest['cases']:
        name = Path(row['input']).stem
        source, output = inputs/Path(row['input']).name, released/Path(row['output']).name
        report = json.loads((released/(name+'.json')).read_text(encoding='utf-8'))
        if digest(source) != row['input_sha256'] or digest(output) != row['output_sha256']:
            raise ValueError(f'Input/output hash mismatch: {name}')
        if report['input_sha256'] != row['input_sha256']:
            raise ValueError(f'Report input hash mismatch: {name}')
        d, v, _ = map(int, source.read_text(encoding='utf-8-sig').splitlines()[0].split())
        rows = [list(map(int, line.split())) for line in output.read_text().splitlines()]
        if (len(rows) != d or any(not r or r[0] != len(r)-1 for r in rows) or
                sorted(vehicle for r in rows for vehicle in r[1:]) != list(range(v))):
            raise ValueError(f'Invalid allocation: {name}')
        if [r[1:] for r in rows] != report['allocation'] or report['allocation'] != frontend[name]['allocation']:
            raise ValueError(f'Allocation differs between output/report/webpage: {name}')
        for key in ('objective', 'penalty_multiplier', 'distance_scale', 'score'):
            if report[key] != frontend[name][key]:
                raise ValueError(f'Scoring definition differs in frontend: {name}/{key}')
        if report['objective'] != 'getscore' or report['penalty_multiplier'] != 2 or report['distance_scale'] != 1000:
            raise ValueError(f'Wrong scoring target: {name}')
        validation = report['validation']
        if validation['status'] != row['status'] or report['score'] != row['score']:
            raise ValueError(f'Manifest differs from validation: {name}')
        if validation['status'] == 'validated':
            if validation['trials'] != manifest['requested_trials']:
                raise ValueError(f'Wrong validation trial count: {name}')
            for candidate in validation['candidates']:
                scores = candidate['scores']
                if (len(scores) != validation['trials'] or median(scores) != candidate['median'] or
                        min(scores) != candidate['minimum'] or max(scores) != candidate['maximum']):
                    raise ValueError(f'Invalid trial summary: {name}')
            selected = next(r for r in validation['candidates'] if r['name'] == validation['selected'])
            if selected['median'] != report['score'] or frontend[name]['source'] != validation['selected']:
                raise ValueError(f'Selected score differs: {name}')
        elif report['score'] is not None:
            raise ValueError(f'Unvalidated fallback must not carry a new score: {name}')
    return len(names)


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--released', type=Path, default=here/'released')
    parser.add_argument('--inputs', type=Path, default=here/'input_data')
    parser.add_argument('--web-data', type=Path, default=here.parent/'docs/published.js')
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    count = verify(args.released, args.inputs, args.web_data, allow_incomplete=args.allow_incomplete)
    print(f'PASS: {count} submitted allocations match input hashes, reports and webpage data.')


if __name__ == '__main__':
    main()
