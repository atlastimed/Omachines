"""Bounded probe IO and process-group ownership, separate from VM workers."""
import atexit
import os
import selectors
import signal
import subprocess
import threading
import time

LIMIT = 2 * 1024 * 1024
_lock = threading.RLock()
_active = set()
_closing = False
_handlers_installed = False
_launching_on_main = False
_deferred_signal = None


class ProbeError(Exception):
    pass


def install_shutdown_handlers():
    global _handlers_installed
    if _handlers_installed or threading.current_thread() is not threading.main_thread():
        return
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _shutdown_signal)
    _handlers_installed = True


def _shutdown_signal(signum, _frame):
    global _deferred_signal
    if _launching_on_main:
        _deferred_signal = signum
        return
    close_all()
    raise SystemExit(128 + signum)


def close_all():
    global _closing
    with _lock:
        _closing = True
        children = list(_active)
    for child in children:
        child.close()


atexit.register(close_all)


class Process:
    def __init__(self, command, *, stdin=False):
        global _launching_on_main
        install_shutdown_handlers()
        self._close_lock = threading.RLock()
        self.closed = False
        # Defer main-thread termination across spawn/register. Blocking the OS
        # signal mask instead would pass blocked TERM/HUP to the executable.
        main = threading.current_thread() is threading.main_thread()
        if main:
            _launching_on_main = True
        try:
            with _lock:
                if _closing:
                    raise ProbeError("Probe owner is shutting down")
                self.proc = subprocess.Popen(command, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             start_new_session=True, bufsize=0)
                _active.add(self)
            os.set_blocking(self.proc.stdout.fileno(), False)
            if stdin:
                os.set_blocking(self.proc.stdin.fileno(), False)
        finally:
            if main:
                _launching_on_main = False
                if _deferred_signal is not None:
                    _shutdown_signal(_deferred_signal, None)

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            # Reap only after killing remaining group members. A promptly
            # exiting leader must not spare a descendant that ignores TERM.
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                time.sleep(0.1)
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # SIGKILL cannot complete while a process is in uninterruptible
                # kernel IO. Do not hang the desktop waiting for that kernel wait.
                pass
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream:
                    stream.close()
            with _lock:
                _active.discard(self)
            self.closed = True

    def write(self, data, deadline):
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdin, selectors.EVENT_WRITE)
            remaining = memoryview(data)
            while remaining:
                if self.closed or _closing:
                    raise ProbeError("Probe owner is shutting down")
                delay = deadline - time.monotonic()
                if delay <= 0:
                    raise ProbeError("The connection timed out")
                if not selector.select(min(0.2, delay)):
                    continue
                try:
                    sent = os.write(self.proc.stdin.fileno(), remaining)
                except BlockingIOError:
                    continue
                remaining = remaining[sent:]


def output(command, timeout):
    child = Process(command)
    deadline = time.monotonic() + timeout
    data = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.proc.stdout, selectors.EVENT_READ)
            while True:
                if child.closed or _closing:
                    raise ProbeError("Probe owner is shutting down")
                delay = deadline - time.monotonic()
                if delay <= 0:
                    raise ProbeError("The connection timed out")
                if not selector.select(min(0.2, delay)):
                    continue
                chunk = os.read(child.proc.stdout.fileno(), min(65536, LIMIT + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > LIMIT:
                    raise ProbeError("The response exceeded its size limit")
        try:
            status = child.proc.wait(timeout=max(0, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ProbeError("The connection timed out") from None
        if status:
            raise ProbeError("The command did not complete successfully")
        return data.decode("utf-8", errors="replace").strip()
    finally:
        child.close()
