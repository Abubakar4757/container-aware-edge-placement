"""Run the offline demo and audit, or reproduce both complete experiments."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Run both factorial experiments and regenerate figures")
    parser.add_argument("--synthetic", action="store_true", help="Run with synthetic resource profiles")
    parser.add_argument("--seeds", type=int, help="Reduce the seed count for a smoke run")
    parser.add_argument("--output", type=Path, default=Path("runs/reproduction"), help="Full-run output directory")
    args = parser.parse_args()
    if not args.full and (args.synthetic or args.seeds is not None):
        parser.error("--synthetic and --seeds require --full")
    if args.seeds is not None and args.seeds < 1:
        parser.error("--seeds must be positive")
    run("-m", "unittest", "-q")
    if not args.full:
        run("experiment.py", "demo")
        run("verify_results.py")
        return

    output = (ROOT / args.output).resolve()
    single = output / "single-region"
    multi = output / "multi-region"
    if args.synthetic:
        source = ("--synthetic",)
    else:
        local_tables = ROOT / "data/raw"
        if not all(any((local_tables / f"{name}.{extension}").is_file() for extension in ("tar.gz", "csv"))
                   for name in ("machine_meta", "container_meta")):
            run("experiment.py", "fetch")
        run("experiment.py", "calibrate")
        source = ("--calibration", ROOT / "data/derived/calibration.json")
    seed_args = ("--seeds", args.seeds) if args.seeds else ()
    for config, directory in (("experiment.json", single), ("experiment.multiregion.json", multi)):
        run("experiment.py", "run", "--config", config, "--output", directory, *source, *seed_args)
        run("plot_results.py", "--summary", directory / "summary.json", "--output", directory / "benchmark.png")
    run("verify_results.py", single, multi)
    run("plot_research.py", "--single", single / "summary.json", "--multi", multi / "summary.json",
        "--output", output / "figures")


if __name__ == "__main__":
    main()
