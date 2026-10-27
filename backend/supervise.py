"""Run one VM provider command for an Omachines worker.

The worker passes its locked operation.lock to this process, which lives
exactly as long as the provider. A worker that dies early therefore cannot
release the lock while the provider still works. Output is relayed while the
worker reads it and drained afterwards, so the provider never meets a broken
pipe. Usage: supervise.py <provider argv...>
"""
import os
import signal
import subprocess
import sys


def main():
    # A terminal hangup, Ctrl+C or termination reaches the provider directly
    # when it is meant for it; this process only waits for the outcome. A
    # handler, unlike SIG_IGN, is not inherited by the provider.
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: None)
    if os.isatty(1):
        # Output goes straight to the operation terminal, which does not
        # depend on the worker.
        code = subprocess.call(sys.argv[1:])
        raise SystemExit(128-code if code < 0 else code)
    merged = os.path.sameopenfile(1, 2)
    p = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merged else None)
    relay = True
    while chunk := os.read(p.stdout.fileno(), 65536):
        while relay and chunk:
            try:
                chunk = chunk[os.write(1, chunk):]
            except OSError:
                relay = False
    code = p.wait()
    raise SystemExit(128-code if code < 0 else code)


if __name__ == "__main__":
    main()
