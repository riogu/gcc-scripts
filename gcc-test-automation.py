#!/usr/bin/env python3
"""
GCC Testing Workflow - Compare your changes against trunk or previous runs.

Modes:
  full      (default)  full 'make check' (all suites; compares gcc + g++)
  c++-all              check-c++-all (C++ conformance; cacheable trunk baseline)
  analyzer             all analyzer tests across both harnesses (gcc + g++)

Only c++-all participates in the cached trunk baseline (--skip-trunk).
full and analyzer always run a fresh trunk.
"""

import argparse
import subprocess
import sys
import threading
import shutil
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Tuple, List

# ---------------------------------------------------------------- configuration
GCC_DEV_DIR = Path.home() / "gcc-dev"
TEST_RESULTS_DIR = GCC_DEV_DIR / "test-gcc"
GCC_CONFIG_OPTS = ('--disable-bootstrap --enable-checking=yes,rtl '
                   '--enable-languages=c,c++ CFLAGS="-O0 -g3" CXXFLAGS="-O0 -g3"')
BASE_NPROC = 12

# Per-mode dispatch: make invocation + which sums it yields (relative to
# build/gcc/testsuite/) + rough time estimate (build_s, test_s) at BASE_NPROC.
# 'cwd' is 'root' (build/) for the full-tree target, else 'gcc' (build/gcc).
MODES = {
    "full":      {"make": ["check"],                      "cwd": "root",
                  "sums": ["*"],                           "est": (30, 70 * 60)},  # 'full' globs all *.sum; this list is unused
    "c++-all":   {"make": ["check-c++-all"],              "cwd": "gcc",
                  "sums": ["g++/g++.sum"],                 "est": (30, 37 * 60)},
    "analyzer":  {"make": ["check-gcc", "check-g++"],     "cwd": "gcc",
                  "runtestflags": "analyzer.exp",
                  "sums": ["gcc/gcc.sum", "g++/g++.sum"],  "est": (30, 5 * 60)},
    "check-g++": {"make": ["check-g++"],                  "cwd": "gcc",
                  "sums": ["g++/g++.sum"],                 "est": (30, 20 * 60)},
    "targeted":  {"make": ["check-g++"],                  "cwd": "gcc",
                  "sums": ["g++/g++.sum"],                 "est": (900, 60)},
}
# latest-trunk holds whatever you last built as your default baseline; there is
# no per-mode cache. Targeted runs are the only thing that never caches.


class Colors:
    RED, GREEN, YELLOW = '\033[0;31m', '\033[0;32m', '\033[1;33m'
    BLUE, CYAN, MAGENTA, NC = '\033[0;34m', '\033[0;36m', '\033[0;35m', '\033[0m'


def log(level_color, tag, msg, err=False):
    print(f"{level_color}[{tag}]{Colors.NC} {msg}", file=sys.stderr if err else sys.stdout)
def log_info(m):    log(Colors.BLUE, "INFO", m)
def log_success(m): log(Colors.GREEN, "SUCCESS", m)
def log_warning(m): log(Colors.YELLOW, "WARNING", m)
def log_error(m):   log(Colors.RED, "ERROR", m, err=True)
def die(m):         log_error(m); sys.exit(1)


# ---------------------------------------------------------------------- config
@dataclass
class RunConfig:
    mode: str
    nproc: int
    build_target: str = "all"             # 'all' or 'cc1plus'
    runtestflags: Optional[str] = None    # user-supplied targeted flags

    @property
    def effective_mode(self) -> str:
        # User RUNTESTFLAGS override mode-specific commands with a targeted g++ run.
        return "targeted" if self.runtestflags else self.mode

    @property
    def multi_sum(self) -> bool:
        return self.effective_mode in ("full", "analyzer")

    @property
    def force_fresh_trunk(self) -> bool:
        # Only targeted (user RUNTESTFLAGS) runs refuse to cache: a partial
        # dg.exp baseline isn't a meaningful "default" baseline. Every other
        # mode treats latest-trunk as "my current default", built however I
        # last built it -- it's on me to keep mode/config consistent.
        return bool(self.runtestflags)

    @property
    def cacheable(self) -> bool:
        return not self.force_fresh_trunk


# -------------------------------------------------------------------- helpers
def fmt_time(s: int) -> str:
    return f"{s // 60}m {s % 60:02d}s"

def get_nproc(override: Optional[int]) -> int:
    if override:
        return override
    try:
        return max(1, int(subprocess.check_output(["nproc"]).decode().strip()))
    except Exception:
        return 4

def run_command(cmd, log_file: Optional[Path] = None, cwd: Optional[Path] = None,
                shell: bool = False) -> int:
    disp = cmd if isinstance(cmd, str) else ' '.join(cmd)
    log_info(f"Running: {Colors.CYAN}{disp}{Colors.NC}")
    if log_file:
        with open(log_file, 'a') as f:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=cwd, text=True, shell=shell)
            for line in proc.stdout:
                print(line, end=''); f.write(line)
            proc.wait()
            return proc.returncode
    return subprocess.run(cmd, cwd=cwd, text=True, shell=shell).returncode

def git_info(repo: Path) -> Tuple[str, str]:
    def g(*a): return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout.strip()
    return g("rev-parse", "HEAD"), g("branch", "--show-current")

def start_progress(phase: str, estimate: int) -> threading.Event:
    stop, start = threading.Event(), datetime.now()
    def printer():
        while not stop.wait(10):
            el = int((datetime.now() - start).total_seconds())
            if estimate > 0:
                pct = min(99, int(100 * el / estimate))
                fill = min(int(20 * el / estimate), 20)
                bar = '█' * fill + '░' * (20 - fill)
                print(f"\r {Colors.CYAN}[{bar}]{Colors.NC} {Colors.YELLOW}{fmt_time(el)}{Colors.NC}"
                      f" / ~{fmt_time(estimate)} ({pct}%) {phase}\n", end='', flush=True)
            else:
                print(f"\r  {Colors.CYAN}[...]{Colors.NC} {Colors.YELLOW}{fmt_time(el)}{Colors.NC} {phase}\n",
                      end='', flush=True)
        print()
    threading.Thread(target=printer, daemon=True).start()
    return stop


# ----------------------------------------------------------------- build/test
def build_gcc(source_dir: Path, cfg: RunConfig, log_file: Path) -> float:
    build_dir = source_dir / "build"
    if not build_dir.exists():
        import shlex
        build_dir.mkdir(parents=True)
        log_info("Configuring (no existing build dir)...")
        if run_command(["../configure"] + shlex.split(GCC_CONFIG_OPTS), log_file, build_dir) != 0:
            die("Configure failed!")

    start = datetime.now()
    if cfg.build_target == "cc1plus":
        log_info("Building cc1plus only...")
        rc = run_command(["make", f"-j{cfg.nproc}", "cc1plus"], log_file, build_dir / "gcc")
    else:
        log_info("Building full GCC...")
        rc = run_command(["make", f"-j{cfg.nproc}"], log_file, build_dir)
    if rc != 0:
        die("Build failed!")
    dur = (datetime.now() - start).total_seconds()
    log_success(f"Build complete ({fmt_time(int(dur))})")
    return dur

def collect_sums(gcc_build: Path, results_dir: Path, prefix: str, mode: str) -> List[Tuple[str, Path]]:
    """Copy this mode's sums into results_dir as <prefix>-<suite>.sum.

    For 'full', glob every *.sum across the whole build tree (gcc/testsuite
    plus the per-target lib*/testsuite dirs), since 'make check' produces sums
    in two roots and under a host-triple subdir. Other modes use the explicit
    list in MODES (sums are all under build/gcc/testsuite/)."""
    build_root = gcc_build.parent           # build/
    out = []

    if mode == "full":
        # suite label = the sum's stem (gcc, g++, libstdc++, libgomp, ...).
        for src in sorted(build_root.rglob("*.sum")):
            suite = src.stem
            dst = results_dir / f"{prefix}-{suite}.sum"
            shutil.copy(src, dst)
            out.append((suite, dst))
    else:
        ts = gcc_build / "testsuite"
        for rel in MODES[mode]["sums"]:
            src, suite = ts / rel, rel.split("/")[0]
            if src.exists():
                dst = results_dir / f"{prefix}-{suite}.sum"
                shutil.copy(src, dst)
                out.append((suite, dst))
            else:
                log_warning(f"Expected sum missing (suite may not have run): {src}")

    if not out:
        die("No sum files produced; the test run likely failed.")
    log_info(f"Collected {len(out)} sum file(s): {', '.join(s for s, _ in out)}")
    return out

def run_tests(source_dir: Path, results_dir: Path, prefix: str, cfg: RunConfig,
              log_file: Path) -> Tuple[float, List[Tuple[str, Path]]]:
    mode = cfg.effective_mode
    spec = MODES[mode]
    build_dir = source_dir / "build"
    cwd = build_dir if spec["cwd"] == "root" else build_dir / "gcc"
    rtf = cfg.runtestflags or spec.get("runtestflags")

    start = datetime.now()
    log_info(f"Running mode '{mode}': make {' '.join(spec['make'])}"
             + (f" RUNTESTFLAGS=\"{rtf}\"" if rtf else ""))
    base = ["make", "-k", f"-j{cfg.nproc}", *spec["make"]]
    if rtf:
        run_command(f"{' '.join(base)} RUNTESTFLAGS=\"{rtf}\"", log_file, cwd, shell=True)
    else:
        run_command(base, log_file, cwd)

    sums = collect_sums(build_dir / "gcc", results_dir, prefix, mode)
    dur = (datetime.now() - start).total_seconds()
    log_success(f"Tests complete ({fmt_time(int(dur))})")
    return dur, sums

def build_and_test(name: str, source_dir: Path, results_dir: Path,
                   cfg: RunConfig) -> Tuple[dict, str, str, List[Tuple[str, Path]]]:
    commit, branch = git_info(source_dir)
    log_info(f"{name} at commit: {Colors.CYAN}{commit[:12]}{Colors.NC}")
    build_est, test_est = MODES[cfg.effective_mode]["est"]
    scale = lambda s: int(s * BASE_NPROC / cfg.nproc)
    prefix = name.lower()
    timings = {}

    p = start_progress(f"Building {prefix}", scale(build_est))
    timings[f"{prefix}_build"] = build_gcc(source_dir, cfg, results_dir / f"{prefix}-build.log")
    p.set()

    p = start_progress(f"Testing {prefix}", scale(test_est))
    dur, sums = run_tests(source_dir, results_dir, prefix, cfg, results_dir / f"{prefix}-test.log")
    timings[f"{prefix}_test"] = dur
    p.set()
    return timings, branch, commit, sums


# ------------------------------------------------------------------ comparison
def compare_results(baseline: List[Tuple[str, Path]], current: List[Tuple[str, Path]],
                    results_dir: Path, source_dir: Path):
    script = source_dir / "contrib" / "compare_tests"
    if not script.exists():
        log_warning("contrib/compare_tests not found, skipping comparison")
        return
    base_by_suite = dict(baseline)
    for suite, cur in current:
        base = base_by_suite.get(suite)
        if not base or not base.exists():
            log_warning(f"No baseline for suite '{suite}', skipping")
            continue
        log_info(f"Comparing {suite}...")
        out = subprocess.run([str(script), str(base), str(cur)],
                             capture_output=True, text=True).stdout
        (results_dir / f"comparison-{suite}.txt").write_text(out)
        print(f"\n{Colors.MAGENTA}Comparison ({suite}):{Colors.NC}\n{out}")


# ------------------------------------------------------------------ results io
def discover_sums(d: Path, prefix: str) -> List[Tuple[str, Path]]:
    out = []
    for p in sorted(d.glob(f"{prefix}-*.sum")):
        out.append((p.stem[len(prefix) + 1:], p))   # 'trunk-g++' -> 'g++'
    return out

def latest_trunk_commit() -> Optional[str]:
    summ = TEST_RESULTS_DIR / "latest-trunk" / "summary.txt"
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
                  source_sums: List[Tuple[str, Path]], baseline_sums: List[Tuple[str, Path]],
                  baseline_label: str):
    with open(path, 'w') as f:
        f.write("GCC Test Run Summary\n" + "=" * 70 + "\n")
        f.write(f"Timestamp: {ts}\nTest Name: {name}\nSource Directory: {source_dir}\n")
        f.write(f"Source Branch: {branch} ({commit[:12]})\n")
        f.write(f"Test Mode: {cfg.effective_mode}\n")
        f.write(f"Baseline: {baseline_label}\n")
        f.write("\nTiming\n" + "-" * 70 + "\n")
        for k, v in timings.items():
            f.write(f"{k.replace('_', ' ').title()}: {fmt_time(int(v))}\n")
        f.write(f"Total: {fmt_time(int(sum(timings.values())))}\n")
        f.write("\nQuick Stats\n" + "=" * 70 + "\n")

        def stats(sums, tag):
            for suite, p in sums:
                if p.exists():
                    f.write(f"\n{tag} [{suite}]:\n")
                    f.writelines(l for l in open(p) if l.startswith("# of"))
        stats(baseline_sums, "BASELINE")
        stats(source_sums, "TESTBRANCH")

def print_final(results_dir: Path, name: str, cfg: RunConfig, timings: dict):
    print(f"\n{Colors.GREEN}{'='*70}{Colors.NC}")
    log_success("Testing complete!")
    print(f"{Colors.CYAN}Results:{Colors.NC} {results_dir.name}")
    print(f"{Colors.CYAN}Mode:{Colors.NC} {cfg.effective_mode}")
    for k, v in timings.items():
        print(f"  {k}: {Colors.YELLOW}{fmt_time(int(v))}{Colors.NC}")
    print(f"{Colors.CYAN}Diffs:{Colors.NC} ls {TEST_RESULTS_DIR}/latest-{name}/comparison-*.txt")


# ----------------------------------------------------------- baseline resolver
def resolve_baseline(args, cfg: RunConfig, results_dir: Path,
                     timings: dict) -> Tuple[List[Tuple[str, Path]], str]:
    """Produce (baseline_sums, label). May build/test trunk. Empty list = no baseline."""
    trunk_dir = GCC_DEV_DIR / "trunk"

    # compare against a prior run, identified independently of this run's name
    if args.compare_against:
        bdir = find_baseline_run(args.compare_against)
        if not bdir:
            list_previous_names(); die(f"Baseline not found: {args.compare_against}")
        # a baseline dir may hold either a normal run's sums (source-*) or a
        # trunk-only run's sums (trunk-*); accept whichever is present.
        sums = discover_sums(bdir, "source") or discover_sums(bdir, "trunk")
        if not sums:
            die(f"No baseline .sum files in {bdir}")
        log_info(f"Comparing against {bdir.name} (assumed same mode/config)")
        return sums, bdir.name

    # reuse cached trunk
    if args.skip_trunk:
        if cfg.force_fresh_trunk:
            die("--skip-trunk can't be used with --runtestflags (targeted runs aren't cached).")
        sums = discover_sums(TEST_RESULTS_DIR / "latest-trunk", "trunk")
        if not sums:
            die("No cached trunk results. Run --trunk-only first.")
        log_info("Using cached trunk results (assumed same mode/config as this run)")
        return sums, "cached trunk"

    # build trunk fresh (cache it unless this is a targeted run)
    if not trunk_dir.exists():
        die(f"Trunk directory does not exist: {trunk_dir}")
    cur_commit, _ = git_info(trunk_dir)

    if cfg.cacheable and latest_trunk_commit() == cur_commit:
        sums = discover_sums(TEST_RESULTS_DIR / "latest-trunk", "trunk")
        if sums:
            log_info(f"Trunk unchanged at {cur_commit[:12]} - reusing cache")
            return sums, "cached trunk"

    out_dir = (TEST_RESULTS_DIR / f"trunk_{datetime.now():%Y-%m-%d_%H:%M:%S}"
               if cfg.cacheable else results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"{Colors.CYAN}Building/testing trunk...{Colors.NC}")
    tt, tb, tc, sums = build_and_test("Trunk", trunk_dir, out_dir, cfg)
    timings.update(tt)
    if cfg.cacheable:
        symlink_latest(out_dir, "trunk")
        write_trunk_summary(out_dir / "summary.txt", tc, tb, f"{datetime.now():%Y-%m-%d_%H:%M:%S}")
    return sums, f"trunk {tc[:12]}"


# ------------------------------------------------------------------- listings
def find_baseline_run(bid: str) -> Optional[Path]:
    """Find a results dir to use as a baseline, by free-form identifier:
    exact dir name, or any substring match (a name, a timestamp, etc.).
    Independent of the current run's name."""
    exact = TEST_RESULTS_DIR / bid
    if exact.is_dir():
        return exact
    matches = [d for d in TEST_RESULTS_DIR.glob(f"*{bid}*")
               if d.is_dir() and not d.is_symlink()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        log_error(f"Multiple runs match '{bid}':")
        for m in sorted(matches):
            print(f"  - {m.name}")
        log_error("Be more specific (use the full dir name).")
    return None

def list_previous_names():
    if not TEST_RESULTS_DIR.exists():
        print(f"{Colors.YELLOW}No results directory{Colors.NC}"); return
    names = set()
    for d in TEST_RESULTS_DIR.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith("trunk_"):
            parts = d.name.rsplit("_", 2)
            if len(parts) >= 3 and len(parts[-2]) == 10 and len(parts[-1]) == 8:
                names.add(parts[0])
    print(f"\n{Colors.CYAN}Previous test names:{Colors.NC}")
    for n in sorted(names):
        runs = [r for r in TEST_RESULTS_DIR.glob(f"{n}_*") if r.is_dir() and not r.is_symlink()]
        print(f"  - {n} ({len(runs)} runs)")

def list_runs_for_name(name: str):
    runs = sorted((r for r in TEST_RESULTS_DIR.glob(f"{name}_*")
                   if r.is_dir() and not r.is_symlink()), reverse=True)
    print(f"\n{Colors.CYAN}Runs for '{name}':{Colors.NC}")
    for r in runs:
        print(f"  - {r.name.replace(f'{name}_', '')}")


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="GCC testing workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{Colors.CYAN}Modes:{Colors.NC} full (default) | c++-all | analyzer

{Colors.CYAN}Baselines:{Colors.NC} latest-trunk is your default baseline (whatever you last
  built unnamed). --skip-trunk reuses it. A named --trunk-only saves a
  standalone baseline that does NOT touch latest-trunk; reuse it with
  --compare-against. You're responsible for matching mode/config.

{Colors.CYAN}Examples:{Colors.NC}
  # set your default baseline (becomes latest-trunk)
  %(prog)s --trunk-only --mode full -j8

  # test a branch vs that default baseline
  %(prog)s source1 -n my-fix --skip-trunk -j8

  # make a separate named baseline (leaves latest-trunk alone)
  %(prog)s --trunk-only --mode c++-all -n c++-trunk-base -j8

  # compare a branch against the named baseline
  %(prog)s source1 -n my-fix --mode c++-all --compare-against c++-trunk-base

  # one-off targeted run (not cached)
  %(prog)s source1 -n my-fix --runtestflags "analyzer.exp=exception-subclass-*.C"
""")
    ap.add_argument("source_dir", nargs='?')
    ap.add_argument("-n", "--name")
    ap.add_argument("-j", "--nproc", type=int)
    ap.add_argument("--mode", choices=["full", "c++-all", "analyzer"], default="full")
    ap.add_argument("--only-cc1plus", action="store_true")
    ap.add_argument("--only-check-g++", dest="only_check_gpp", action="store_true")
    ap.add_argument("--skip-trunk", action="store_true")
    ap.add_argument("--trunk-only", action="store_true")
    ap.add_argument("--compare-against", metavar="TIMESTAMP")
    ap.add_argument("--runtestflags", metavar="FLAGS")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--list-runs", action="store_true")
    args = ap.parse_args()

    if args.list:
        list_previous_names(); return
    if args.list_runs:
        if not args.name: die("--list-runs requires -n/--name")
        list_runs_for_name(args.name); return

    cfg = RunConfig(
        mode="check-g++" if args.only_check_gpp else args.mode,
        nproc=get_nproc(args.nproc),
        build_target="cc1plus" if args.only_cc1plus else "all",
        runtestflags=args.runtestflags,
    )
    if args.only_cc1plus and cfg.mode in ("full", "analyzer"):
        die("--only-cc1plus needs a full build; use it with c++-all or --only-check-g++.")

    ts = f"{datetime.now():%Y-%m-%d_%H:%M:%S}"

    # ---- trunk-only ----
    if args.trunk_only:
        trunk_dir = GCC_DEV_DIR / "trunk"
        if not trunk_dir.exists(): die(f"Trunk dir missing: {trunk_dir}")
        # Named trunk-only => a standalone baseline (does NOT become latest-trunk).
        # Unnamed => becomes latest-trunk, your default baseline.
        named = bool(args.name)
        dirname = f"{args.name}_{ts}" if named else f"trunk_{ts}"
        rdir = TEST_RESULTS_DIR / dirname; rdir.mkdir(parents=True, exist_ok=True)
        log_info(f"Trunk-only ({cfg.effective_mode})" + (f" -> named baseline '{args.name}'" if named else ""))
        timings, branch, commit, _ = build_and_test("Trunk", trunk_dir, rdir, cfg)
        if named:
            write_trunk_summary(rdir / "summary.txt", commit, branch, ts)
            log_info(f"Saved as '{dirname}'. Use: --compare-against {args.name}")
        elif cfg.cacheable:
            symlink_latest(rdir, "trunk")
            write_trunk_summary(rdir / "summary.txt", commit, branch, ts)
            log_info("Saved as latest-trunk (your default baseline).")
        else:
            log_warning("Targeted run: NOT saved as latest-trunk.")
        log_success(f"Trunk done: {rdir.name}")
        return
        return

    # ---- regular run ----
    if not args.source_dir: list_previous_names(); die("No source directory specified.")
    if not args.name: die("Test name required (-n/--name).")
    source_dir = (Path(args.source_dir) if Path(args.source_dir).is_absolute()
                  else GCC_DEV_DIR / args.source_dir)
    if not source_dir.exists(): die(f"Source dir missing: {source_dir}")

    rdir = TEST_RESULTS_DIR / f"{args.name}_{ts}"; rdir.mkdir(parents=True, exist_ok=True)
    log_info(f"Name: {args.name} | Mode: {cfg.effective_mode} | Source: {source_dir}")

    timings: dict = {}
    baseline_sums, baseline_label = resolve_baseline(args, cfg, rdir, timings)

    log_info(f"{Colors.CYAN}Building/testing your changes...{Colors.NC}")
    st, branch, commit, source_sums = build_and_test("Source", source_dir, rdir, cfg)
    timings.update(st)

    compare_results(baseline_sums, source_sums, rdir, source_dir)
    write_summary(rdir / "summary.txt", args.name, ts, source_dir, cfg, branch, commit,
                  timings, source_sums, baseline_sums, baseline_label)
    symlink_latest(rdir, args.name)
    print_final(rdir, args.name, cfg, timings)


if __name__ == "__main__":
    main()
