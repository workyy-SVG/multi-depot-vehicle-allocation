"""Validate every candidate independently and publish one consistent submission."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from solve import HERE, read_instance, read_allocation, write_allocation, baseline
from validation import validate_candidates
from runtime_control import RuntimeBudget, BudgetExpired
from result_files import atomic_json, atomic_text, score_report


def _validate_file(path, candidates, trials, seconds):
    return validate_candidates(read_instance(path), candidates, trials=trials, seconds=seconds)


def frontend_record(report):
    validation = report['validation']
    return dict(allocation=report['allocation'], score=report['score'],
                source=report['selected_source'], status=validation['status'],
                trials=validation.get('trials', 0),
                seconds_per_depot=validation.get('seconds_per_depot'),
                candidates=validation.get('candidates', []),
                objective=report['objective'], penalty_multiplier=report['penalty_multiplier'],
                distance_scale=report['distance_scale'], input_sha256=report['input_sha256'])


def publish(paths, outdir, candidate_dirs, *, trials=3, seconds=3.0,
            time_budget=None, web_data=None):
    paths = [Path(path) for path in paths]
    target_names = {path.stem for path in paths}
    if len(target_names) != len(paths):
        raise ValueError('Input stems must be unique within a publication.')
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    budget = RuntimeBudget(time_budget)
    records, cases = {}, {}
    retained = []
    previous_manifest = outdir/'manifest.json'
    if previous_manifest.is_file():
        previous = json.loads(previous_manifest.read_text(encoding='utf-8'))
        for case in previous['cases']:
            name = Path(case['input']).stem
            if name in target_names:
                continue
            report = json.loads((outdir/(name+'.json')).read_text(encoding='utf-8'))
            if hashlib.sha256((outdir/case['output']).read_bytes()).hexdigest() != case['output_sha256']:
                raise ValueError(f'Previously published output was modified: {name}')
            validation = report['validation']
            if validation['status'] == 'validated' and (
                    validation['trials'] != trials or validation['seconds_per_depot'] != seconds):
                raise ValueError('Republish all cases when changing validation trials or runtime.')
            records[name] = frontend_record(report)
            cases[name] = case
            retained.append(name)
    started = perf_counter()
    for path in paths:
        path = Path(path)
        data = read_instance(path)
        candidates = {}
        # A previously published allocation is the conservative tie-breaking anchor.
        for folder in [outdir, *candidate_dirs]:
            folder = Path(folder)
            source = folder / (path.stem + '.out')
            if source.is_file():
                label = (folder.relative_to(HERE).as_posix() if folder.is_relative_to(HERE)
                         else folder.name)
                if label in candidates:
                    label = folder.as_posix()
                candidates[label] = read_allocation(source, data)
        if not candidates:
            candidates['mean_greedy'] = baseline(data, data['hist'], 'mean')
        fallback_source = next(iter(candidates))
        allocation = candidates[fallback_source]
        # Always leave a legal .out even if a native solver exceeds the deadline.
        write_allocation(outdir / (path.stem + '.out'), allocation, preserve_order=True)
        try:
            checked = budget.run(_validate_file, path, candidates, trials, seconds)
        except BudgetExpired:
            checked = None
            report = score_report(path, data, allocation, source=fallback_source)
            print(f'{path.name}: budget exhausted; feasible fallback is NOT newly validated.', flush=True)
        else:
            allocation = checked['allocation']
            report = score_report(path, data, allocation, validation=checked)
            print(f'{path.name}: {checked["selected"]}, median={report["score"]:.3f}, trials={trials}', flush=True)
        output_path = outdir / (path.stem + '.out')
        write_allocation(output_path, allocation, preserve_order=True)
        atomic_json(outdir / (path.stem + '.json'), report)
        records[path.stem] = frontend_record(report)
        cases[path.stem] = dict(input=path.name, input_sha256=report['input_sha256'],
                             output=output_path.name,
                             output_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
                             selected_source=report['selected_source'], score=report['score'],
                             status=report['validation']['status'],
                             trials=report['validation'].get('trials', 0))
    manifest = [cases[name] for name in sorted(cases)]
    complete = all(row['status'] == 'validated' for row in manifest)
    summary = dict(schema_version=1, objective='getscore', penalty_multiplier=2,
                   distance_scale=1000, seconds_per_depot=seconds,
                   requested_trials=trials, time_budget=time_budget,
                   elapsed_seconds=perf_counter() - started, complete=complete,
                   updated_cases=sorted(target_names), retained_cases=retained,
                   cases=manifest)
    atomic_json(outdir / 'manifest.json', summary)
    if web_data:
        atomic_text(web_data, 'const publishedResults = ' + json.dumps(records, ensure_ascii=False,
                                                                     allow_nan=False) + ';\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=HERE/'input_data')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--output-dir', type=Path, default=HERE/'released')
    parser.add_argument('--candidate-dir', type=Path, action='append', default=[],
                        help='Additional search output, alongside all shipped references')
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=3.0)
    parser.add_argument('--time-budget', type=float,
                        help='Shared native-solver budget; report/file I/O is outside the hard stop')
    parser.add_argument('--web-data', type=Path, default=HERE.parent/'docs/published.js')
    args = parser.parse_args()
    if args.trials < 1 or args.seconds <= 0 or (args.time_budget is not None and args.time_budget <= 0):
        parser.error('Trials, seconds and time budget must be positive.')
    paths = [args.file] if args.file else sorted(args.input_dir.glob('*.in'))
    if not paths:
        parser.error('No .in files found.')
    defaults = [HERE/'score_optimization/allocations', HERE/'score_output',
                HERE/'score_benchmark', HERE/'output']
    candidates = [*defaults, *(folder.resolve() for folder in args.candidate_dir)]
    report = publish(paths, args.output_dir.resolve(), candidates, trials=args.trials,
                     seconds=args.seconds, time_budget=args.time_budget, web_data=args.web_data)
    if not report['complete']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
