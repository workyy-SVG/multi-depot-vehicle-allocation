"""Recheck shortlisted allocations under identical three-second scoring."""

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import median

from optimize import initialize, measure, read_allocation, read_instance, write_allocation
from score_attachment import evaluate


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def collect(path, depot_count, vehicle_count, references, searches, top):
    candidates = {}
    for folder in references:
        source = folder / path.with_suffix(".out").name
        if source.is_file():
            candidates[folder.name] = read_allocation(source, depot_count, vehicle_count)
    for index, folder in enumerate(searches, 1):
        for rank in range(1, top + 1):
            source = folder / "candidates" / f"{path.stem}__rank_{rank}.out"
            if not source.is_file():
                break
            candidates[f"search_{index}_rank_{rank}"] = read_allocation(
                source, depot_count, vehicle_count)
    return candidates


def verify_case(path, references, searches, outdir, trials, top, workers):
    periods, capacities, locations, history = read_instance(path)
    candidates = collect(path, len(locations), len(capacities), references, searches, top)
    if not candidates:
        raise ValueError(f"No candidates for {path.name}")
    keys = {name: tuple(tuple(capacities[v] for v in group) for group in groups)
            for name, groups in candidates.items()}
    jobs = sorted({(depot, fleet, 3.0) for key in keys.values()
                   for depot, fleet in enumerate(key)})
    with ProcessPoolExecutor(workers, initializer=initialize,
                             initargs=(periods, locations, history)) as pool:
        sampled = [dict(zip(jobs, pool.map(measure, jobs))) for _ in range(trials)]
    scores = {name: [sum(values[d, fleet, 3.0] for d, fleet in enumerate(key))
                     for values in sampled] for name, key in keys.items()}
    winner = min(scores, key=lambda name: (median(scores[name]), name.startswith("search_")))
    write_allocation(outdir / "allocations" / path.with_suffix(".out").name, candidates[winner])
    rows = [(path.stem, name, *values, median(values)) for name, values in scores.items()]
    preferred = [name for name in ("output", "score_output", "score_benchmark") if name in scores]
    published = min(preferred or list(scores), key=lambda name: median(scores[name]))
    result = (path.stem, winner, median(scores[winner]), published, median(scores[published]),
              median(scores.get("output", scores[published])))
    print(result, flush=True)
    return result, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--reference", action="append", type=Path, default=[])
    parser.add_argument("--search", action="append", type=Path, default=[])
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--scorer", type=Path)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    paths = sorted(args.input.glob("*.in")) if args.input.is_dir() else [args.input]
    summary, observations = [], []
    for path in paths:
        result, case_rows = verify_case(path, args.reference, args.search, args.outdir,
                                        args.trials, args.top, args.workers)
        summary.append(result)
        observations.extend(case_rows)
    write_csv(args.outdir / "scores.csv",
              ("instance", "selected", "median_score", "best_repository_reference",
               "repository_median_score", "original_median_score"), summary)
    write_csv(args.outdir / "trials.csv",
              ("instance", "candidate", *(f"trial_{n+1}" for n in range(args.trials)), "median"),
              observations)
    if args.scorer:
        checks = []
        references = {path.name: path for path in args.reference}
        for path, result in zip(paths, summary):
            filename = path.with_suffix(".out").name
            chosen = args.outdir / "allocations" / filename
            baseline = references.get(result[3])
            baseline_path = baseline / filename if baseline else chosen
            same = chosen.read_bytes().split() == baseline_path.read_bytes().split()
            score = evaluate(args.scorer, path, chosen)
            reference_score = score if same else evaluate(args.scorer, path, baseline_path)
            checks.append((path.stem, score, reference_score, same))
            print(f"Attachment check: {checks[-1]}", flush=True)
        write_csv(args.outdir / "attachment.csv",
                  ("instance", "selected_score", "reference_score", "same_allocation"), checks)


if __name__ == "__main__":
    main()
