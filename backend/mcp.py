"""Bounded, short-lived stdio MCP probes. Never persist remote output."""
import json
import os
import selectors
import signal
import subprocess
import time


class ProbeError(Exception):
    pass


class Client:
    def __init__(self, command, timeout=15):
        self.timeout = timeout
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.seq = 0

    def __enter__(self):
        try:
            self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "omachines", "version": "0.1.0"}})
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, message):
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method, params):
        self.seq += 1
        self.send({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        total = 0
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if message.get("id") != self.seq:
                    continue
                if "error" in message:
                    raise ProbeError("MCP request refused")
                result = message.get("result", {})
                if result.get("isError"):
                    raise ProbeError("MCP tool failed")
                return result
            if not self.sel.select(max(0, min(0.5, deadline - time.monotonic()))):
                if self.proc.poll() is not None:
                    break
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                raise ProbeError("MCP response exceeded limit")
            self.buffer += chunk
        raise ProbeError("MCP check timed out or disconnected")

    def call(self, name, arguments=None):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def __exit__(self, *_):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        self.sel.close()
        for stream in (self.proc.stdin, self.proc.stdout):
            stream.close()
