"""Entry point: argument validation and dispatch."""

from pathlib import Path
from datetime import datetime

from . import ui
from .cli import build_parser
from .config import RunConfig, get_nproc, GCC_DEV_DIR, TEST_RESULTS_DIR, TRUNK_DIR
from .engine import build_and_test, compare_results
from .baseline import (resolve_baseline, write_summary, write_trunk_summary,
                       symlink_latest, list_previous_names, list_runs_for_name)


def _final(results_dir: Path, name: str, cfg: RunConfig, timings: dict):
    print(f"\n{ui.Colors.GREEN}{'='*70}{ui.Colors.NC}")
    ui.success("Testing complete!")
    print(f"{ui.Colors.CYAN}Results:{ui.Colors.NC} {results_dir.name}")
    print(f"{ui.Colors.CYAN}Mode:{ui.Colors.NC} {cfg.effective_mode}")
    for k, v in timings.items():
        print(f"  {k}: {ui.Colors.YELLOW}{ui.fmt_time(int(v))}{ui.Colors.NC}")
    print(f"{ui.Colors.CYAN}Diffs:{ui.Colors.NC} ls {TEST_RESULTS_DIR}/latest-{name}/comparison-*.txt")


def main():
    args = build_parser().parse_args()

    if args.list:
        list_previous_names(); return
    if args.list_runs:
        if not args.name: ui.die("--list-runs requires -n/--name")
        list_runs_for_name(args.name); return

    cfg = RunConfig(
        mode="check-g++" if args.only_check_gpp else args.mode,
        nproc=get_nproc(args.nproc),
        build_target="cc1plus" if args.only_cc1plus else "all",
        runtestflags=args.runtestflags,
    )
    if args.only_cc1plus and cfg.mode in ("full", "analyzer"):
        ui.die("--only-cc1plus needs a full build; use it with c++-all or --only-check-g++.")

    ts = f"{datetime.now():%Y-%m-%d_%H:%M:%S}"

    # ---- trunk-only ----
    if args.trunk_only:
        if not TRUNK_DIR.exists(): ui.die(f"Trunk dir missing: {TRUNK_DIR}")
        named = bool(args.name)
        rdir = TEST_RESULTS_DIR / (f"{args.name}_{ts}" if named else f"trunk_{ts}")
        rdir.mkdir(parents=True, exist_ok=True)
        ui.info(f"Trunk-only ({cfg.effective_mode})"
                + (f" -> named baseline '{args.name}'" if named else ""))
        timings, branch, commit, _ = build_and_test("Trunk", TRUNK_DIR, rdir, cfg)
        if named:
            write_trunk_summary(rdir / "summary.txt", commit, branch, ts)
            ui.info(f"Saved as '{rdir.name}'. Use: --compare-against {args.name}")
        elif cfg.cacheable:
            symlink_latest(rdir, "trunk")
            write_trunk_summary(rdir / "summary.txt", commit, branch, ts)
            ui.info("Saved as latest-trunk (your default baseline).")
        else:
            ui.warning("Targeted run: NOT saved as latest-trunk.")
        ui.success(f"Trunk done: {rdir.name}")
        return

    # ---- regular run ----
    if not args.source_dir:
        list_previous_names(); ui.die("No source directory specified.")
    if not args.name:
        ui.die("Test name required (-n/--name).")
    source_dir = (Path(args.source_dir) if Path(args.source_dir).is_absolute()
                  else GCC_DEV_DIR / args.source_dir)
    if not source_dir.exists():
        ui.die(f"Source dir missing: {source_dir}")

    rdir = TEST_RESULTS_DIR / f"{args.name}_{ts}"
    rdir.mkdir(parents=True, exist_ok=True)
    ui.info(f"Name: {args.name} | Mode: {cfg.effective_mode} | Source: {source_dir}")

    timings: dict = {}
    baseline_sums, baseline_label = resolve_baseline(args, cfg, rdir, timings)

    ui.info(f"{ui.Colors.CYAN}Building/testing your changes...{ui.Colors.NC}")
    st, branch, commit, source_sums = build_and_test("Source", source_dir, rdir, cfg)
    timings.update(st)

    compare_results(baseline_sums, source_sums, rdir, source_dir)
    write_summary(rdir / "summary.txt", args.name, ts, source_dir, cfg, branch, commit,
                  timings, source_sums, baseline_sums, baseline_label)
    symlink_latest(rdir, args.name)
    _final(rdir, args.name, cfg, timings)


if __name__ == "__main__":
    main()
