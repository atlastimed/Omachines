.pragma library

function gib(value) { return typeof value === "number" ? (value / 1073741824).toFixed(1) + " GiB" : "Not checked" }
function freeSpace(value) { return typeof value === "number" ? gib(value) + " free" : "Not checked" }
function checked(state, name) { return state && state.checks && state.checks[name] ? state.checks[name] : {status:"unknown", message:"Not checked yet"} }
// An Omachines operation, or an omarchy-vm lifecycle command started outside
// the panel (maintenance), disables the same controls.
function active(state) { var op = state.operation || {}; return op.status === "running" || op.status === "starting" || !!state.maintenance }
function ago(value, now) {
  var time = Date.parse(value || "")
  if (!isFinite(time)) return "Not checked"
  var seconds = Math.max(0, Math.floor((now - time) / 1000))
  return seconds < 60 ? "Just now" : seconds < 3600 ? Math.floor(seconds / 60) + " min ago" : Math.floor(seconds / 3600) + " h ago"
}
function isOmarchy(state) { return !!state && state.provider === "omarchy-qemu" }
function name(state) { return state && state.name ? state.name : "the VM" }
// Nerd Font glyphs: Windows logo, Arch logo (Omarchy is Arch-based), monitor.
function machineIcon(provider) { return provider === "omarchy-qemu" ? "" : provider === "omarchy-windows" ? "" : "󰍹" }
function machines(state) { return (state && state.machines || []).filter(function(m) { return m && m.id }) }
function on(state) { return ["running", "starting", "paused", "stopping", "error"].indexOf(state) >= 0 }
function stateWord(row) {
  return ({running:"Running", starting:"Starting", paused:"Paused", stopping:"Stopping", error:"Attention", checking:"Checking", unknown:"Unknown"})[row.state] || "Off"
}
function nextMachine(state) {
  var rows = machines(state)
  if (rows.length < 2) return ""
  var index = rows.map(function(m) { return m.id }).indexOf(state.machineId)
  return rows[(index + 1) % rows.length].id
}
function machineName(state, id) {
  var row = machines(state).filter(function(m) { return m.id === id })[0]
  return row ? row.name : ""
}
// Compact bar label: the glyph of each running VM, so either VM being on is
// visible at a glance. Vertical bars stack the glyphs.
function barText(state, vertical, showLabel) {
  var running = machines(state).filter(function(m) { return m.active === true || (m.active === undefined && on(m.state)) })
  var icons = running.map(function(m) { return machineIcon(m.provider) })
  if (vertical || !showLabel) return icons.length ? icons.join(vertical ? "\n" : " ") : "󰍹"
  return "󰍹 VM" + (icons.length ? " " + icons.join(" ") : "") + (active(state) ? "…" : "")
}
function tooltip(state) {
  var rows = machines(state)
  if (!rows.length) return state.summary || "Checking machines"
  return rows.map(function(m) { return m.name + ": " + (m.summary || stateWord(m)) }).join(" · ")
}
function resourceLine(state) {
  var r = state.resources || {}
  return r.cpu ? r.cpu + " CPUs · " + gib(r.memoryBytes) + " RAM" : isOmarchy(state) ? "Omarchy Linux VM" : "Omarchy Windows VM"
}
// The configured allocation, so a confirmation shows the change it makes.
function allocationNow(state) {
  var a = state.allocation || {}, ram = String(a.ram || "").match(/^(\d+)G$/i)
  return a.cpu && ram ? "Currently " + a.cpu + " CPUs and " + ram[1] + " GiB RAM. " : ""
}
function confirmationText(action, p, state) {
  var n = name(state)
  if (isOmarchy(state)) return ({
    stop: "Shut down " + n + "? Save your work first. Guest apps and agent tasks will stop.",
    backup: "Back up " + n + "? It shuts down while its disk is copied and verified, then starts again if it was running. Save your work first. No password is needed.",
    allocate: "Apply " + p.cpu + " CPUs and " + p.ram + " GiB RAM to " + n + "? " + allocationNow(state) + "It restarts if it is running; save your work first.",
    restore: "Restore backup " + p.backupId + "? " + n + " stops and its disk is replaced with this backup; the current disk is kept aside. A terminal asks you to type the backup identity first."
  })[action] || "Continue?"
  return ({
    allocate: "Apply " + p.cpu + " CPUs and " + p.ram + " GiB RAM? " + allocationNow(state) + "Running Windows apps will close. Windows restarts if it was running. A terminal asks for your Omarchy password.",
    rehearse: "Test backup " + p.backupId + " in a separate offline VM using 4 GiB RAM? The original Windows VM is preserved. Review the captured boot screen before recording success. Temporary files remain for inspection.",
    rollback: "Return Windows to the storage retained before restoration? Windows will stop. The terminal asks you to confirm; the replaced storage is also retained.",
    restore: "Restore backup " + p.backupId + "? Windows will stop. The disk, TPM and firmware are replaced with this backup; current storage is retained for recovery. The terminal verifies space and integrity and asks you to type the backup identity before replacement.",
    "rehearsal-pass": "Have you opened the rehearsal image and confirmed that the restored Windows copy reached its desktop or sign-in screen? This records your review and stops only the disposable VM.",
    "rehearsal-fail": "Record that the offline boot failed and stop the disposable VM? Its files remain for inspection.",
    backup: "Close or save your Windows work first. This will shut down Windows, copy and verify its complete state, then start it again. A terminal asks for your Omarchy password.",
    stop: "Shut down Windows? Save your work first. Windows apps and agent tasks will stop."
  })[action] || "Return a disconnected Windows session to its console? This may change where the desktop is displayed. It will not unlock a locked session."
}
function actionRows(tab, state, aside) {
  var caps = state.capabilities || {}, running = state.state === "running", rows = [], n = name(state), linux = isOmarchy(state)
  var asideRows = aside && caps.asideSupported === true
  if (tab === "Overview") {
    rows.push({action:"open",label:"Open desktop",detail:"Start " + n + " or focus its viewer",icon:"󰍹",enabled:!!caps.viewer})
    if (asideRows) rows.push({action:"aside",label:"Open Aside",detail:"Use Aside in the Windows desktop",icon:"󰖟",enabled:!!caps.aside && running})
    rows.push({action:"shared",label:"Shared folder",detail:"Files shared with " + n,icon:"󰉋",enabled:caps.shared !== false})
    rows.push({action:"start",label:"Start in background",detail:"Start " + n + " without a viewer",icon:"󰐊",enabled:!!caps.lifecycle && !running && !(linux && on(state.state))})
    rows.push({action:"stop",label:"Shut down " + n + "…",detail:linux ? "Close guest apps and release its RAM" : "Close Windows apps and release its RAM",icon:"󰐥",enabled:!!caps.lifecycle && (running || state.state === "starting" || (linux && on(state.state)))})
  } else if (tab === "Agents") {
    rows.push({action:"refresh",label:"Check connections",detail:asideRows ? "Check commands, desktop tools and Aside" : "Check commands and desktop tools",icon:"󰑐",enabled:true})
    if (caps.handoff === true) rows.push({action:"handoff",label:"Return desktop to console…",detail:"Recover a disconnected agent desktop",icon:"󰑓",enabled:!!caps.ssh && running})
    if (caps.console === true) rows.push({action:"console",label:"Open recovery console",detail:"Use if the normal viewer cannot connect",icon:"󰆍",enabled:true})
    rows.push({action:"connections",label:"Agent connection guide",detail:"Existing command and MCP connections; no credentials",icon:"󰈙",enabled:true})
    if (caps.catalogue === true) rows.push({action:"inspect",label:"Refresh VM configuration",detail:"Read protected configuration; asks for your Omarchy password",icon:"󰑐",enabled:!!caps.admin})
    rows.push({action:"diagnostics",label:"Save diagnostic report",detail:"Connection results without credentials",icon:"󰈙",enabled:true})
  } else if (tab === "Backups") {
    rows.push({action:"backup",label:"Create complete backup…",detail:linux ? "Stops " + n + " temporarily; no password needed" : caps.backup ? "Stops Windows temporarily; asks for your Omarchy password" : "Set the Windows setup workspace in plugin settings",icon:"󰁯",enabled:!!caps.backup})
    if (caps.catalogue === true) rows.push({action:"inspect",label:"Refresh backup catalogue",detail:"Find existing archives; asks for your Omarchy password",icon:"󰑐",enabled:!!caps.admin})
  }
  return rows
}
function agentChecks(state, aside) {
  var caps = state.capabilities || {}
  return aside && caps.asideSupported === true ? ["ssh","desktop","cua","aside","asideCli","asideAuth"] : ["ssh","desktop","cua"]
}
function agentNote(state) {
  return isOmarchy(state) ? "Agents get passwordless sudo inside " + name(state) + ", can use its desktop and signed-in accounts, and can read and write the shared folder. Readiness does not show agent activity."
    : "Agents use the Windows desktop and can access its signed-in accounts. Aside model authentication and usage are not checked here."
}
function settingsNote(state) {
  return isOmarchy(state) ? "More options are in Omarchy’s bar widget settings. Resource changes need no password and restart " + name(state) + " if it is running."
    : "More options are in Omarchy’s bar widget settings. Resource changes require administrator approval and may restart Windows."
}
