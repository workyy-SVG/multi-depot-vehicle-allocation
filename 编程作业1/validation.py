"""Independent, ordered-fleet validation shared by search and publication."""
from math import isfinite
from statistics import median
from time import perf_counter


def validate_candidates(data, candidates, *, trials=3, seconds=3.0, seed=0):
    from solve import check_allocation
    from score_search import ScoreEvaluator, DISTANCE_SCALE, PENALTY_MULTIPLIER

    if trials < 1 or seconds <= 0 or not candidates:
        raise ValueError('Positive trials/runtime and at least one candidate are required.')
    for groups in candidates.values():
        check_allocation(data, groups)
    names = list(candidates)
    observations = {name: [] for name in names}
    started = perf_counter()
    profile = dict(preprocess_seconds=0.0, model_seconds=0.0, solve_seconds=0.0,
                   solver_calls=0)
    # Each trial has a new evaluator: no search cache or prior trial is reused.
    for trial in range(trials):
        evaluator = ScoreEvaluator(data, seed=seed)
        rotation = trial % len(names)
        for name in names[rotation:] + names[:rotation]:
            value = evaluator.score(candidates[name], seconds, output_order=True)
            if not isfinite(value):
                raise RuntimeError(f'No feasible scored solution for candidate {name!r}.')
            observations[name].append(value)
        for key, value in evaluator.profile.items():
            profile[key] += value
    rows = [dict(name=name, scores=observations[name], median=median(observations[name]),
                 minimum=min(observations[name]), maximum=max(observations[name]))
            for name in names]
    anchor = names[0]
    # A challenger must improve the median and win a majority of paired trials.
    eligible = [row for row in rows if row['name'] == anchor or (
        row['median'] < rows[0]['median'] - 0.001 and
        sum(a < b - 0.001 for a, b in zip(row['scores'], observations[anchor])) > trials // 2)]
    winner = min(eligible, key=lambda row: (row['median'], names.index(row['name'])))
    return dict(selected=winner['name'], allocation=candidates[winner['name']],
                candidates=rows, trials=trials, seconds_per_depot=seconds, seed=seed,
                objective='getscore', distance_scale=DISTANCE_SCALE,
                penalty_multiplier=PENALTY_MULTIPLIER, profile=profile,
                elapsed_seconds=perf_counter() - started, status='validated')
