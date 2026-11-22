"""The installed app's engine: the same program as `python -m orbit`, frozen into orbit-engine.exe by PyInstaller.

The window (orbit.exe) starts this with `--voice --ipc --ipc-port N --exit-with-parent`; run by hand it behaves exactly like
`python -m orbit`.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()          # a frozen program that starts a worker process must say so, or it starts itself again
    from orbit.cli import main

    sys.exit(main())
