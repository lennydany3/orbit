"""Orbit stops when the window that started it goes away."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from orbit.ipc.lifetime import watch_parent

SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_the_callback_runs_when_the_stream_ends_and_not_before():
    r, w = os.pipe()
    gone = threading.Event()
    watch_parent(r, gone.set, poll_s=0.02)
    os.write(w, b"anything the window might send is ignored")
    assert not gone.wait(0.3)
    os.close(w)
    assert gone.wait(3)
    os.close(r)


def test_a_closed_stream_counts_as_gone():
    r, w = os.pipe()
    gone = threading.Event()
    os.close(w)
    os.close(r)                                      # asking about a closed descriptor fails: still just "gone"
    watch_parent(r, gone.set, poll_s=0.02)
    assert gone.wait(3)


@pytest.mark.skipif(sys.platform != "win32", reason="a file reads as ended on other systems")
def test_something_that_is_not_a_pipe_never_counts_as_the_window_going_away(tmp_path):
    # Run from a terminal with --exit-with-parent, standard input is a console (or a file): Orbit must not quit at once.
    path = tmp_path / "stdin.txt"
    path.write_bytes(b"")
    fd = os.open(path, os.O_RDONLY)
    gone = threading.Event()
    watch_parent(fd, gone.set, poll_s=0.02)
    assert not gone.wait(0.5)
    os.close(fd)


def test_a_real_child_is_interrupted_like_ctrl_c_when_its_parent_closes_the_pipe():
    code = (
        "import _thread, sys, time\n"
        "from orbit.ipc.lifetime import watch_parent\n"
        "watch_parent(sys.stdin.fileno(), _thread.interrupt_main)\n"
        "import scipy.linalg\n"                           # a native library whose start-up looks at standard input: this froze Orbit's voice load
        "print('ready', flush=True)\n"
        "try:\n"
        "    for _ in range(600):\n"                     # like the voice loop, which wakes every 50 ms
        "        time.sleep(0.05)\n"
        "    print('NOT interrupted', flush=True)\n"
        "except KeyboardInterrupt:\n"
        "    print('interrupted', flush=True)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                             env={**os.environ, "PYTHONPATH": SRC})
    watchdog = threading.Timer(25, child.kill)               # a hang must fail the test, not stall it
    watchdog.start()
    try:
        assert child.stdout.readline().strip() == "ready", "the child never finished loading its libraries"
        started = time.perf_counter()
        child.stdin.close()                          # the window went away
        assert child.stdout.readline().strip() == "interrupted"
        assert time.perf_counter() - started < 3.0
        assert child.wait(timeout=10) == 0
    finally:
        watchdog.cancel()
        if child.poll() is None:
            child.kill()
