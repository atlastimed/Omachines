import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

BACKEND = Path(__file__).resolve().parents[1]/"backend"
sys.path.insert(0, str(BACKEND))
import core
from mcp import Client, ProbeError


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()[0] != "Z"
    except FileNotFoundError:
        return False


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def wait_file(self, path):
        deadline = time.monotonic()+4
        while not path.exists() and time.monotonic()<deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), "fixture failed to start")

    def assert_dead(self, pid):
        deadline = time.monotonic()+2
        while alive(pid) and time.monotonic()<deadline:
            time.sleep(0.01)
        self.assertFalse(alive(pid), "probe process survived cleanup")

    def test_bounded_output_and_nonzero_exit(self):
        self.assertEqual(core.run([sys.executable, "-c", "print('ready')"], 2), "ready")
        with self.assertRaisesRegex(core.OperationError, "successfully"):
            core.run([sys.executable, "-c", "raise SystemExit(7)"], 2)

    def test_oversized_no_newline_output_stopped_before_producer_finishes(self):
        complete = self.path/"complete"
        code = "import sys; from pathlib import Path; sys.stdout.buffer.write(b'x'*(8*1024*1024)); sys.stdout.flush(); Path(sys.argv[1]).touch()"
        with self.assertRaisesRegex(core.OperationError, "size limit"):
            core.run([sys.executable, "-c", code, str(complete)], 3)
        self.assertFalse(complete.exists())

    def test_timeout_kills_child(self):
        pidfile = self.path/"pid"
        code = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(10)"
        with self.assertRaisesRegex(core.OperationError, "timed out"):
            core.run([sys.executable, "-c", code, str(pidfile)], 0.3)
        self.assert_dead(int(pidfile.read_text()))

    def test_eof_without_exit_still_has_a_deadline(self):
        code = "import os,time; os.close(1); time.sleep(10)"
        with self.assertRaisesRegex(core.OperationError, "timed out"):
            core.run([sys.executable, "-c", code], 0.2)

    def test_mcp_kills_descendant_even_when_leader_exits_on_term(self):
        pidfile = self.path/"descendant"
        descendant = "import os,signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(10)"
        leader = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdout=subprocess.DEVNULL); time.sleep(10)"
        client = Client([sys.executable, "-c", leader, descendant, str(pidfile)], 1)
        try:
            self.wait_file(pidfile)
        finally:
            client.__exit__()
            client.__exit__()  # cleanup is idempotent
        self.assert_dead(int(pidfile.read_text()))

    def test_owner_shutdown_cleans_probe_group(self):
        pidfile = self.path/"probe"
        probe = "import os,signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(10)"
        owner = "import sys; sys.path.insert(0,sys.argv[1]); import core; core.run([sys.executable,'-c',sys.argv[2],sys.argv[3]],2)"
        p = subprocess.Popen([sys.executable, "-B", "-c", owner, str(BACKEND), probe, str(pidfile)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_file(pidfile)
            p.terminate()
            p.wait(timeout=3)
            self.assert_dead(int(pidfile.read_text()))
        finally:
            if p.poll() is None:
                p.kill(); p.wait()
            if pidfile.exists() and alive(int(pidfile.read_text())):
                os.killpg(int(pidfile.read_text()), signal.SIGKILL)

    def test_termination_signal_is_not_blocked_in_probes(self):
        ready, terminated = self.path/"ready", self.path/"terminated"
        code = """import signal,sys,time
from pathlib import Path
def done(*_):
    Path(sys.argv[2]).touch()
    raise SystemExit(0)
signal.signal(signal.SIGTERM, done)
Path(sys.argv[1]).touch()
time.sleep(10)
"""
        client = Client([sys.executable, "-c", code, str(ready), str(terminated)], 1)
        try:
            self.wait_file(ready)
        finally:
            client.__exit__()
        self.assertTrue(terminated.exists(), "probe could not handle graceful termination")

    def test_threaded_owner_exit_cleans_probes_but_preserves_lifecycle_worker(self):
        probe_file, worker_file = self.path/"probe", self.path/"worker"
        sleeper = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(10)"
        owner = """import sys,subprocess,concurrent.futures,time
sys.path.insert(0,sys.argv[1])
import core, probes
probes.install_shutdown_handlers()
subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[4]],start_new_session=True)
pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
pool.submit(core.run,[sys.executable,'-c',sys.argv[2],sys.argv[3]],20)
try:
    time.sleep(10)
finally:
    probes.close_all()
    pool.shutdown(wait=True)
"""
        p = subprocess.Popen([sys.executable,"-B","-c",owner,str(BACKEND),sleeper,str(probe_file),str(worker_file)],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            self.wait_file(probe_file); self.wait_file(worker_file)
            p.terminate(); p.wait(timeout=3)
            self.assert_dead(int(probe_file.read_text()))
            self.assertTrue(alive(int(worker_file.read_text())), "detached lifecycle worker was killed")
        finally:
            if p.poll() is None: p.kill(); p.wait()
            for file in (probe_file,worker_file):
                if file.exists() and alive(int(file.read_text())):
                    os.killpg(int(file.read_text()),signal.SIGKILL)

    def test_mcp_rejects_non_object_envelopes_and_results(self):
        values = [None, [], 7, "text", {"jsonrpc":"2.0","id":1,"result":None},
                  {"jsonrpc":"2.0","id":1,"result":[]}, {"jsonrpc":"2.0","id":1}]
        server = "import sys; sys.stdin.readline(); print(sys.argv[1],flush=True)"
        for value in values:
            with self.subTest(value=value), self.assertRaises(ProbeError):
                with Client([sys.executable, "-c", server, json.dumps(value)], 1):
                    self.fail("invalid response accepted")

    def test_mcp_oversized_line_is_bounded(self):
        server = "import sys; sys.stdin.readline(); sys.stdout.buffer.write(b'x'*(8*1024*1024)); sys.stdout.flush()"
        with self.assertRaisesRegex(ProbeError, "limit"):
            with Client([sys.executable, "-c", server], 2):
                self.fail("oversized response accepted")


if __name__ == "__main__": unittest.main()
