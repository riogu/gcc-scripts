"""Core build / test / compare execution."""

import shlex
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

from . import ui
from .config import RunConfig, MODES, GCC_CONFIG_OPTS, BASE_NPROC, IGNORE_PATTERNS

Sums = List[Tuple[str, Path]]   # list of (suite_label, sum_path)


def run_command(cmd, log_file: Optional[Path] = None, cwd: Optional[Path] = None,
                shell: bool = False) -> int:
    disp = cmd if isinstance(cmd, str) else ' '.join(cmd)
    ui.info(f"Running: {ui.Colors.CYAN}{disp}{ui.Colors.NC}")
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
    def g(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout.strip()
    return g("rev-parse", "HEAD"), g("branch", "--show-current")


# ------------------------------------------------------------------ build/test
def build_gcc(source_dir: Path, cfg: RunConfig, log_file: Path) -> float:
    build_dir = source_dir / "build"
    if not build_dir.exists():
        build_dir.mkdir(parents=True)
        ui.info("Configuring (no existing build dir)...")
        if run_command(["../configure"] + shlex.split(GCC_CONFIG_OPTS), log_file, build_dir) != 0:
            ui.die("Configure failed!")

    start = datetime.now()
    if cfg.build_target == "cc1plus":
        ui.info("Building cc1plus only...")
        rc = run_command(["make", f"-j{cfg.nproc}", "cc1plus"], log_file, build_dir / "gcc")
    else:
        ui.info("Building full GCC...")
        rc = run_command(["make", f"-j{cfg.nproc}"], log_file, build_dir)
    if rc != 0:
        ui.die("Build failed!")
    dur = (datetime.now() - start).total_seconds()
    ui.success(f"Build complete ({ui.fmt_time(int(dur))})")
    return dur


def collect_sums(gcc_build: Path, results_dir: Path, prefix: str, mode: str) -> Sums:
    """Copy this mode's sums into results_dir as <prefix>-<suite>.sum.

    'full' globs every *.sum across the build tree (sums live in two roots and
    under a host-triple subdir). Other modes use the explicit MODES list under
    build/gcc/testsuite/."""
    build_root = gcc_build.parent           # build/
    out: Sums = []

    if mode == "full":
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
                ui.warning(f"Expected sum missing (suite may not have run): {src}")

    if not out:
        ui.die("No sum files produced; the test run likely failed.")
    ui.info(f"Collected {len(out)} sum file(s): {', '.join(s for s, _ in out)}")
    return out


def run_tests(source_dir: Path, results_dir: Path, prefix: str, cfg: RunConfig,
              log_file: Path) -> Tuple[float, Sums]:
    mode = cfg.effective_mode
    spec = MODES[mode]
    build_dir = source_dir / "build"
    cwd = build_dir if spec["cwd"] == "root" else build_dir / "gcc"
    rtf = cfg.runtestflags or spec.get("runtestflags")

    start = datetime.now()
    ui.info(f"Running mode '{mode}': make {' '.join(spec['make'])}"
            + (f" RUNTESTFLAGS=\"{rtf}\"" if rtf else ""))
    base = ["make", "-k", f"-j{cfg.nproc}", *spec["make"]]
    if rtf:
        run_command(f"{' '.join(base)} RUNTESTFLAGS=\"{rtf}\"", log_file, cwd, shell=True)
    else:
        run_command(base, log_file, cwd)

    sums = collect_sums(build_dir / "gcc", results_dir, prefix, mode)
    dur = (datetime.now() - start).total_seconds()
    ui.success(f"Tests complete ({ui.fmt_time(int(dur))})")
    return dur, sums


def build_and_test(name: str, source_dir: Path, results_dir: Path,
                   cfg: RunConfig) -> Tuple[dict, str, str, Sums]:
    commit, branch = git_info(source_dir)
    ui.info(f"{name} at commit: {ui.Colors.CYAN}{commit[:12]}{ui.Colors.NC}")
    build_est, test_est = MODES[cfg.effective_mode]["est"]
    scale = lambda s: int(s * BASE_NPROC / cfg.nproc)
    prefix = name.lower()
    timings: dict = {}

    p = ui.start_progress(f"Building {prefix}", scale(build_est))
    timings[f"{prefix}_build"] = build_gcc(source_dir, cfg, results_dir / f"{prefix}-build.log")
    p.set()

    p = ui.start_progress(f"Testing {prefix}", scale(test_est))
    dur, sums = run_tests(source_dir, results_dir, prefix, cfg, results_dir / f"{prefix}-test.log")
    timings[f"{prefix}_test"] = dur
    p.set()
    return timings, branch, commit, sums


# ------------------------------------------------------------------ comparison
def compare_results(baseline: Sums, current: Sums, results_dir: Path, source_dir: Path):
    script = source_dir / "contrib" / "compare_tests"
    if not script.exists():
        ui.warning("contrib/compare_tests not found, skipping comparison")
        return
    base_by_suite = dict(baseline)
    for suite, cur in current:
        base = base_by_suite.get(suite)
        if not base or not base.exists():
            ui.warning(f"No baseline for suite '{suite}', skipping")
            continue
        ui.info(f"Comparing {suite}...")
        out = subprocess.run([str(script), str(base), str(cur)],
                             capture_output=True, text=True).stdout
        if IGNORE_PATTERNS:
            out = "\n".join(l for l in out.splitlines()
                            if not any(p in l for p in IGNORE_PATTERNS))
        (results_dir / f"comparison-{suite}.txt").write_text(out)
        print(f"\n{ui.Colors.MAGENTA}Comparison ({suite}):{ui.Colors.NC}\n{out}")
