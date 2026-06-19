import subprocess
import shutil
import shlex
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from config import GCC_CONFIG_OPTS, MODES, BASE_NPROC, RunConfig
from ui import log_info, log_success, log_warning, start_progress, fmt_time

def run_command(cmd, log_file: Path = None, cwd: Path = None, shell: bool = False) -> int:
    if log_file:
        with open(log_file, 'a') as f:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, text=True, shell=shell)
            for line in proc.stdout:
                print(line, end=''); f.write(line)
            return proc.wait()
    return subprocess.run(cmd, cwd=cwd, text=True, shell=shell).returncode

def git_info(repo: Path) -> Tuple[str, str]:
    def g(*a): return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout.strip()
    return g("rev-parse", "HEAD"), g("branch", "--show-current")

def build_gcc(source_dir: Path, cfg: RunConfig, log_file: Path) -> float:
    build_dir = source_dir / "build"
    if not build_dir.exists():
        build_dir.mkdir(parents=True)
        log_info("Configuring (no existing build dir)...")
        if run_command(["../configure"] + shlex.split(GCC_CONFIG_OPTS), log_file, build_dir) != 0:
            raise RuntimeError("Configure failed!")

    start = datetime.now()
    if cfg.build_target == "cc1plus":
        rc = run_command(["make", f"-j{cfg.nproc}", "cc1plus"], log_file, build_dir / "gcc")
    else:
        rc = run_command(["make", f"-j{cfg.nproc}"], log_file, build_dir)
    if rc != 0:
        raise RuntimeError("Build failed!")
    return (datetime.now() - start).total_seconds()

def collect_sums(gcc_build: Path, results_dir: Path, prefix: str, mode: str) -> List[Tuple[str, Path]]:
    out = []
    if mode == "full":
        for src in sorted(gcc_build.parent.rglob("*.sum")):
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
    return out

def run_tests(source_dir: Path, results_dir: Path, prefix: str, cfg: RunConfig, log_file: Path) -> Tuple[float, List[Tuple[str, Path]]]:
    mode = cfg.effective_mode
    spec = MODES[mode]
    build_dir = source_dir / "build"
    cwd = build_dir if spec["cwd"] == "root" else build_dir / "gcc"
    rtf = cfg.runtestflags or spec.get("runtestflags")

    start = datetime.now()
    base = ["make", "-k", f"-j{cfg.nproc}", *spec["make"]]
    if rtf:
        run_command(f"{' '.join(base)} RUNTESTFLAGS=\"{rtf}\"", log_file, cwd, shell=True)
    else:
        run_command(base, log_file, cwd)

    sums = collect_sums(build_dir / "gcc", results_dir, prefix, mode)
    return (datetime.now() - start).total_seconds(), sums

def build_and_test(name: str, source_dir: Path, results_dir: Path, cfg: RunConfig) -> Tuple[dict, str, str, List[Tuple[str, Path]]]:
    commit, branch = git_info(source_dir)
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
