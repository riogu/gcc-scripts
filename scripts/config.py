"""RunConfig, MODES, paths, and state constants."""

import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------- paths/consts
GCC_DEV_DIR = Path.home() / "gcc-dev"
TEST_RESULTS_DIR = GCC_DEV_DIR / "test-gcc"
TRUNK_DIR = GCC_DEV_DIR / "trunk"
LATEST_TRUNK = TEST_RESULTS_DIR / "latest-trunk"

GCC_CONFIG_OPTS = ('--disable-bootstrap --enable-checking=yes,rtl '
                   '--enable-languages=c,c++ CFLAGS="-O0 -g3" CXXFLAGS="-O0 -g3"')
BASE_NPROC = 12

# Comparison-time noise filter: tests matching these substrings are dropped
# from comparison output (they still RUN; this only de-noises the diff).
# g++.dg/modules races non-deterministically under parallelism.
IGNORE_PATTERNS = ["g++.dg/modules/"]

# Per-mode dispatch. 'make' = targets; 'cwd' = 'root' (build/) or 'gcc'
# (build/gcc); 'sums' = sum paths under build/gcc/testsuite/ (ignored for
# 'full', which globs every *.sum); 'est' = (build_s, test_s) at BASE_NPROC.
MODES = {
    "full":      {"make": ["check"],                  "cwd": "root",
                  "sums": ["*"],                       "est": (30, 70 * 60)},
    "c++-all":   {"make": ["check-c++-all"],          "cwd": "gcc",
                  "sums": ["g++/g++.sum"],             "est": (30, 37 * 60)},
    "analyzer":  {"make": ["check-gcc", "check-g++"], "cwd": "gcc",
                  "runtestflags": "analyzer.exp",
                  "sums": ["gcc/gcc.sum", "g++/g++.sum"], "est": (30, 5 * 60)},
    "check-g++": {"make": ["check-g++"],              "cwd": "gcc",
                  "sums": ["g++/g++.sum"],             "est": (30, 20 * 60)},
    "targeted":  {"make": ["check-g++"],              "cwd": "gcc",
                  "sums": ["g++/g++.sum"],             "est": (900, 60)},
}


def get_nproc(override: Optional[int]) -> int:
    if override:
        return override
    try:
        return max(1, int(subprocess.check_output(["nproc"]).decode().strip()))
    except Exception:
        return 4


@dataclass
class RunConfig:
    mode: str
    nproc: int
    build_target: str = "all"             # 'all' or 'cc1plus'
    runtestflags: Optional[str] = None    # user-supplied targeted flags

    @property
    def effective_mode(self) -> str:
        # User RUNTESTFLAGS override mode commands with a targeted g++ run.
        return "targeted" if self.runtestflags else self.mode

    @property
    def force_fresh_trunk(self) -> bool:
        # Only targeted runs refuse to cache: a partial dg.exp baseline isn't a
        # meaningful default. Every other mode treats latest-trunk as "my
        # current default", built however I last built it.
        return bool(self.runtestflags)

    @property
    def cacheable(self) -> bool:
        return not self.force_fresh_trunk
