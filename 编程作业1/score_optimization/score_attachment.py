"""Run the supplied getScore function with integer units for PyVRP 0.12.2."""

import argparse
import importlib.util
from pathlib import Path

from pyvrp import Model

from optimize import SCALE, read_allocation


def evaluate(scorer, input_path, output_path):
    header = input_path.read_text(encoding="utf-8-sig").splitlines()[0]
    depots, vehicles, periods = map(int, header.split())
    read_allocation(output_path, depots, vehicles)

    class CompatibleModel(Model):
        def add_vehicle_type(self, *, capacity, num_available):
            return super().add_vehicle_type(capacity=[capacity * periods],
                                            num_available=num_available)

        def add_client(self, *, x, y, demand, required, prize):
            return super().add_client(x, y, delivery=[round(float(demand) * periods)],
                                      required=required, prize=round(float(prize) * SCALE))

        def add_edge(self, frm, to, *, distance):
            return super().add_edge(frm, to, distance=round(float(distance) * SCALE))

        def solve(self, stop):
            result = super().solve(stop, seed=0, collect_stats=False, display=False)

            class ScaledResult:
                def cost(self):
                    return result.cost() / SCALE

            return ScaledResult()

    spec = importlib.util.spec_from_file_location("supplied_scorer", scorer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.Model = CompatibleModel
    return float(module.evaluate_student_solution(input_path, output_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scorer", required=True, type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"{evaluate(args.scorer, args.input, args.output):.9f}")


if __name__ == "__main__":
    main()
