import QtQuick
import Quickshell.Io

Item {
  id: root
  property var shell: null
  property var manifest: null
  property var snapshot: ({ state: "checking", summary: "Checking machines", checks: {}, resources: {}, capabilities: {}, backups: [], operation: {}, machines: [] })
  property bool connected: false
  property string serviceError: ""
  property var configuration: ({})
  property int failures: 0
  readonly property string pluginDir: decodeURIComponent(Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "")).replace(/\/$/, "")

  function send(message) {
    if (!daemon.running) { serviceError = "Omachines is reconnecting"; return }
    daemon.write(JSON.stringify(message) + "\n")
  }
  function configure(value) {
    if (JSON.stringify(value) === JSON.stringify(configuration)) return
    configuration = value
    send({cmd: "configure", settings: value})
  }
  function refresh() { send({cmd: "refresh"}) }
  // machineId pins the request to the machine the panel showed; empty means
  // the currently selected machine.
  function action(name, confirmed, parameters, machineId) {
    var message = {cmd: "action", action: name, confirmed: confirmed === true, parameters: parameters || {}}
    if (machineId) message.machineId = String(machineId)
    send(message)
  }
  function select(machineId) { send({cmd: "select", machineId: String(machineId || "")}) }

  Process {
    id: daemon
    command: ["/usr/bin/python3", "-B", root.pluginDir + "/backend/omachinesctl", "daemon"]
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(data) {
        try {
          const result = JSON.parse(data)
          if (typeof result !== "object" || !result.state) return
          root.snapshot = result
          root.connected = true
          root.serviceError = ""
        } catch (_) { root.serviceError = "Could not read the status response" }
      }
    }
    stderr: SplitParser { onRead: function(data) { /* Never forward raw command output to shell logs. */ } }
    onStarted: {
      root.send({cmd: "configure", settings: root.configuration})
      stable.restart()
    }
    // Quickshell exposes QProcess::ExitStatus without its static Qt type.
    // qmllint disable signal-handler-parameters
    onExited: {
      root.connected = false
      root.serviceError = "Omachines service stopped; reconnecting"
      stable.stop()
      root.failures++
      restart.interval = Math.min(60000, 2000 * Math.pow(2, Math.min(root.failures, 5)))
      restart.restart()
    }
  }
  // qmllint enable signal-handler-parameters
  Timer { id: stable; interval: 60000; onTriggered: root.failures = 0 }
  Timer { id: restart; interval: 2000; onTriggered: daemon.running = true }
  Component.onCompleted: daemon.running = true
  Component.onDestruction: { restart.stop(); daemon.running = false }
}
