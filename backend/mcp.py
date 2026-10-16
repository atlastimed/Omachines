"""Bounded, short-lived stdio MCP probes. Never persist remote output."""
import json
import os
import selectors
import time
from probes import LIMIT, Process, ProbeError


class Client:
    def __init__(self, command, timeout=15):
        self.timeout = timeout
        self.child = Process(command, stdin=True)
        self.proc = self.child.proc
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

    def send(self, message, deadline=None):
        self.child.write((json.dumps(message) + "\n").encode(),
                         deadline if deadline is not None else time.monotonic()+self.timeout)

    def request(self, method, params):
        self.seq += 1
        deadline = time.monotonic() + self.timeout
        self.send({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params}, deadline)
        total = len(self.buffer)
        while time.monotonic() < deadline:
            if self.child.closed:
                raise ProbeError("Probe owner is shutting down")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    raise ProbeError("Malformed MCP response") from None
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ProbeError("Invalid MCP response envelope")
                if message.get("id") != self.seq:
                    continue
                if type(message.get("id")) is not int or ("result" not in message and "error" not in message):
                    raise ProbeError("Invalid MCP response envelope")
                if "error" in message:
                    raise ProbeError("MCP request refused")
                result = message.get("result", {})
                if not isinstance(result, dict):
                    raise ProbeError("Invalid MCP result")
                if result.get("isError"):
                    raise ProbeError("MCP tool failed")
                return result
            if not self.sel.select(max(0, min(0.5, deadline - time.monotonic()))):
                if self.proc.poll() is not None:
                    break
                continue
            chunk = os.read(self.proc.stdout.fileno(), min(65536, LIMIT+1-total))
            if not chunk:
                break
            total += len(chunk)
            if total > LIMIT:
                raise ProbeError("MCP response exceeded limit")
            self.buffer += chunk
        raise ProbeError("MCP check timed out or disconnected")

    def call(self, name, arguments=None):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def __exit__(self, *_):
        self.sel.close()
        self.child.close()
