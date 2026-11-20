"""Orbit stops when the window that started it goes away."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from orbit.ipc.lifetime import watch_parent

SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_the_callback_runs_when_the_stream_ends_and_not_before():
    r, w = os.pipe()
    gone = threading.Event()
    watch_parent(os.fdopen(r, "rb"), gone.set)
    os.write(w, b"anything the window might send is ignored")
    assert not gone.wait(0.3)
    os.close(w)
    assert gone.wait(3)


def test_a_closed_stream_counts_as_gone():
    r, w = os.pipe()
    stream = os.fdopen(r, "rb")
    gone = threading.Event()
    os.close(w)
    stream.close()                                   # reading a closed file raises: still just "gone"
    watch_parent(stream, gone.set)
    assert gone.wait(3)


def test_a_real_child_is_interrupted_like_ctrl_c_when_its_parent_closes_the_pipe():
    code = (
        "import _thread, sys, time\n"
        "from orbit.ipc.lifetime import watch_parent\n"
        "watch_parent(sys.stdin.buffer, _thread.interrupt_main)\n"
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
    try:
        assert child.stdout.readline().strip() == "ready"
        started = time.perf_counter()
        child.stdin.close()                          # the window went away
        assert child.stdout.readline().strip() == "interrupted"
        assert time.perf_counter() - started < 3.0
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.kill()
