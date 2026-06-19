"""Colors, logging, and the background progress thread."""

import sys
import threading
from datetime import datetime


class Colors:
    TTY = sys.stdout.isatty()
    RED, GREEN, YELLOW = ('\033[0;31m', '\033[0;32m', '\033[1;33m') if TTY else ('', '', '')
    BLUE, CYAN, MAGENTA, NC = ('\033[0;34m', '\033[0;36m', '\033[0;35m', '\033[0m') if TTY else ('', '', '', '')


def _log(color, tag, msg, err=False):
    print(f"{color}[{tag}]{Colors.NC} {msg}", file=sys.stderr if err else sys.stdout)


def log_info(m):    _log(Colors.BLUE, "INFO", m)
def log_success(m): _log(Colors.GREEN, "SUCCESS", m)
def log_warning(m): _log(Colors.YELLOW, "WARNING", m)
def log_error(m):   _log(Colors.RED, "ERROR", m, err=True)


def die(m):
    error(m)
    sys.exit(1)


def fmt_time(seconds: int) -> str:
    return f"{seconds // 60}m {seconds % 60:02d}s"


def start_progress(phase: str, estimate: int) -> threading.Event:
    """Print an elapsed/estimate progress line every 10s until the returned
    Event is set. estimate <= 0 prints a spinner-style elapsed-only line."""
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
