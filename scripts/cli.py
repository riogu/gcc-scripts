"""Argparse definition and help/UI text."""

import argparse
from .ui import Colors


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{Colors.CYAN}Modes:{Colors.NC} full (default) | c++-all | analyzer

{Colors.CYAN}Baselines:{Colors.NC} latest-trunk is the default baseline (whatever was last built unnamed). --skip-trunk reuses it.
  A named --trunk-only saves a standalone baseline that does NOT touch latest-trunk (reuse it with --compare-against).

{Colors.BLUE}Examples:{Colors.NC}
{Colors.CYAN} # set your default baseline (becomes latest-trunk) {Colors.NC}
  %(prog)s --trunk-only --mode full -j8

{Colors.CYAN} # test a branch vs that default baseline {Colors.NC}
  %(prog)s source1 -n my-fix --skip-trunk -j8

{Colors.CYAN} # make a separate named baseline (leaves latest-trunk alone) {Colors.NC}
  %(prog)s --trunk-only --mode c++-all -n c++-trunk-base -j8

{Colors.CYAN} # compare a branch against the named baseline {Colors.NC}
  %(prog)s source1 -n my-fix --mode c++-all --compare-against c++-trunk-base

{Colors.CYAN} # one-off targeted run (not cached) {Colors.NC}
  %(prog)s source1 -n my-fix --runtestflags "analyzer.exp=exception-subclass-*.C"
""")
    ap.add_argument("source_dir", nargs='?', help="branch source dir (under gcc-dev/ or absolute)")
    ap.add_argument("-n", "--name", help="name for this run's results")
    ap.add_argument("-j", "--nproc", type=int, help="parallel jobs (default: nproc)")
    ap.add_argument("--mode", choices=["full", "c++-all", "analyzer"], default="full")
    ap.add_argument("--only-cc1plus", action="store_true", help="build only cc1plus (c++-all/check-g++ only)")
    ap.add_argument("--only-check-g++", dest="only_check_gpp", action="store_true",
                    help="run check-g++ (single std level) instead of the mode's target")
    ap.add_argument("--skip-trunk", action="store_true", help="reuse cached latest-trunk baseline")
    ap.add_argument("--trunk-only", action="store_true", help="build/test trunk only")
    ap.add_argument("--compare-against", metavar="ID", help="compare against a prior run (name or timestamp substring)")
    ap.add_argument("--runtestflags", metavar="FLAGS", help="targeted RUNTESTFLAGS (g++ harness; not cached)")
    ap.add_argument("--list", action="store_true", help="list previous run names")
    ap.add_argument("--list-runs", action="store_true", help="list runs for -n NAME")
    ap.add_argument("--diff", nargs=2, metavar=("BASE", "CURR"),
                    help="compare two existing result dirs; no build/test")
    return ap
