"""Baseline resolution: cache symlinks, history lookups, summaries."""

from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

from . import ui
from .config import RunConfig, TEST_RESULTS_DIR, TRUNK_DIR, LATEST_TRUNK
from .engine import build_and_test, git_info, Sums


# ------------------------------------------------------------------ results io
def discover_sums(d: Path, prefix: str) -> Sums:
    out: Sums = []
    for p in sorted(d.glob(f"{prefix}-*.sum")):
        out.append((p.stem[len(prefix) + 1:], p))   # 'trunk-g++' -> 'g++'
    return out


def latest_trunk_commit() -> Optional[str]:
    summ = LATEST_TRUNK / "summary.txt"
    if summ.exists():
        for line in open(summ):
            if line.startswith("Trunk Commit:"):
                return line.split(":", 1)[1].strip()
    return None


def symlink_latest(results_dir: Path, name: str):
    link = TEST_RESULTS_DIR / f"latest-{name}"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(results_dir)


def write_trunk_summary(path: Path, commit: str, branch: str, ts: str):
    path.write_text(f"Trunk Commit: {commit}\nTrunk Branch: {branch}\nTimestamp: {ts}\n")


def write_summary(path: Path, name: str, ts: str, source_dir: Path, cfg: RunConfig,
                  branch: str, commit: str, timings: dict,
                  source_sums: Sums, baseline_sums: Sums, baseline_label: str):
    with open(path, 'w') as f:
        f.write("GCC Test Run Summary\n" + "=" * 70 + "\n")
        f.write(f"Timestamp: {ts}\nTest Name: {name}\nSource Directory: {source_dir}\n")
        f.write(f"Source Branch: {branch} ({commit[:12]})\n")
        f.write(f"Test Mode: {cfg.effective_mode}\n")
        f.write(f"Baseline: {baseline_label}\n")
        f.write("\nTiming\n" + "-" * 70 + "\n")
        for k, v in timings.items():
            f.write(f"{k.replace('_', ' ').title()}: {ui.fmt_time(int(v))}\n")
        f.write(f"Total: {ui.fmt_time(int(sum(timings.values())))}\n")
        f.write("\nQuick Stats\n" + "=" * 70 + "\n")

        def stats(sums, tag):
            for suite, p in sums:
                if p.exists():
                    f.write(f"\n{tag} [{suite}]:\n")
                    f.writelines(l for l in open(p) if l.startswith("# of"))
        stats(baseline_sums, "BASELINE")
        stats(source_sums, "TESTBRANCH")


# ------------------------------------------------------------------- listings
def find_baseline_run(bid: str) -> Optional[Path]:
    """Find a results dir to use as a baseline by free-form identifier (exact
    dir name or substring), independent of the current run's name."""
    exact = TEST_RESULTS_DIR / bid
    if exact.is_dir():
        return exact
    matches = [d for d in TEST_RESULTS_DIR.glob(f"*{bid}*")
               if d.is_dir() and not d.is_symlink()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ui.error(f"Multiple runs match '{bid}':")
        for m in sorted(matches):
            print(f"  - {m.name}")
        ui.error("Be more specific (use the full dir name).")
    return None


def list_previous_names():
    if not TEST_RESULTS_DIR.exists():
        print(f"{ui.Colors.YELLOW}No results directory{ui.Colors.NC}"); return
    names = set()
    for d in TEST_RESULTS_DIR.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith("trunk_"):
            parts = d.name.rsplit("_", 2)
            if len(parts) >= 3 and len(parts[-2]) == 10 and len(parts[-1]) == 8:
                names.add(parts[0])
    print(f"\n{ui.Colors.CYAN}Previous test names:{ui.Colors.NC}")
    for n in sorted(names):
        runs = [r for r in TEST_RESULTS_DIR.glob(f"{n}_*") if r.is_dir() and not r.is_symlink()]
        print(f"  - {n} ({len(runs)} runs)")


def list_runs_for_name(name: str):
    runs = sorted((r for r in TEST_RESULTS_DIR.glob(f"{name}_*")
                   if r.is_dir() and not r.is_symlink()), reverse=True)
    print(f"\n{ui.Colors.CYAN}Runs for '{name}':{ui.Colors.NC}")
    for r in runs:
        print(f"  - {r.name.replace(f'{name}_', '')}")


# ----------------------------------------------------------- baseline resolver
def resolve_baseline(args, cfg: RunConfig, results_dir: Path,
                     timings: dict) -> Tuple[Sums, str]:
    """Produce (baseline_sums, label). May build/test trunk. Empty = no baseline."""
    # compare against a prior run, identified independently of this run's name
    if args.compare_against:
        bdir = find_baseline_run(args.compare_against)
        if not bdir:
            list_previous_names(); ui.die(f"Baseline not found: {args.compare_against}")
        sums = discover_sums(bdir, "source") or discover_sums(bdir, "trunk")
        if not sums:
            ui.die(f"No baseline .sum files in {bdir}")
        ui.info(f"Comparing against {bdir.name} (assumed same mode/config)")
        return sums, bdir.name

    # reuse cached trunk
    if args.skip_trunk:
        if cfg.force_fresh_trunk:
            ui.die("--skip-trunk can't be used with --runtestflags (targeted runs aren't cached).")
        sums = discover_sums(LATEST_TRUNK, "trunk")
        if not sums:
            ui.die("No cached trunk results. Run --trunk-only first.")
        ui.info("Using cached trunk results (assumed same mode/config as this run)")
        return sums, "cached trunk"

    # build trunk fresh (cache it unless this is a targeted run)
    if not TRUNK_DIR.exists():
        ui.die(f"Trunk directory does not exist: {TRUNK_DIR}")
    cur_commit, _ = git_info(TRUNK_DIR)

    if cfg.cacheable and latest_trunk_commit() == cur_commit:
        sums = discover_sums(LATEST_TRUNK, "trunk")
        if sums:
            ui.info(f"Trunk unchanged at {cur_commit[:12]} - reusing cache")
            return sums, "cached trunk"

    out_dir = (TEST_RESULTS_DIR / f"trunk_{datetime.now():%Y-%m-%d_%H:%M:%S}"
               if cfg.cacheable else results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ui.info(f"{ui.Colors.CYAN}Building/testing trunk...{ui.Colors.NC}")
    tt, tb, tc, sums = build_and_test("Trunk", TRUNK_DIR, out_dir, cfg)
    timings.update(tt)
    if cfg.cacheable:
        symlink_latest(out_dir, "trunk")
        write_trunk_summary(out_dir / "summary.txt", tc, tb, f"{datetime.now():%Y-%m-%d_%H:%M:%S}")
    return sums, f"trunk {tc[:12]}"
