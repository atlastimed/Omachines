.pragma library

function gib(value) { return typeof value === "number" ? (value / 1073741824).toFixed(1) + " GiB" : "Not checked" }
function freeSpace(value) { return typeof value === "number" ? gib(value) + " free" : "Not checked" }
function checked(state, name) { return state && state.checks && state.checks[name] ? state.checks[name] : {status:"unknown", message:"Not checked yet"} }
function active(state) { var op = state.operation || {}; return op.status === "running" || op.status === "starting" }
function ago(value, now) {
  var time = Date.parse(value || "")
  if (!isFinite(time)) return "Not checked"
  var seconds = Math.max(0, Math.floor((now - time) / 1000))
  return seconds < 60 ? "Just now" : seconds < 3600 ? Math.floor(seconds / 60) + " min ago" : Math.floor(seconds / 3600) + " h ago"
}
function resourceLine(state) {
  var r = state.resources || {}
  return r.cpu ? r.cpu + " CPUs · " + gib(r.memoryBytes) + " RAM" : "Omarchy Windows VM"
}
function actionRows(tab, state, aside) {
  var caps = state.capabilities || {}, running = state.state === "running", rows = []
  if (tab === "Overview") {
    rows.push({action:"open",label:"Open desktop",detail:"Start Windows or focus its viewer",icon:"󰍹",enabled:!!caps.viewer})
    if (aside) rows.push({action:"aside",label:"Open Aside",detail:"Use Aside in the Windows desktop",icon:"󰖟",enabled:!!caps.aside && running})
    rows.push({action:"shared",label:"Shared folder",detail:"Files shared with Windows",icon:"󰉋",enabled:true})
    rows.push({action:"start",label:"Start in background",detail:"Start Windows without a viewer",icon:"󰐊",enabled:!!caps.omarchy && !running})
    rows.push({action:"stop",label:"Shut down Windows…",detail:"Close Windows apps and release its RAM",icon:"󰐥",enabled:!!caps.omarchy && (running || state.state === "starting")})
  } else if (tab === "Agents") {
    rows.push({action:"refresh",label:"Check connections",detail:"Check commands, desktop tools and Aside",icon:"󰑐",enabled:true})
    rows.push({action:"handoff",label:"Return desktop to console…",detail:"Recover a disconnected agent desktop",icon:"󰑓",enabled:!!caps.ssh && running})
    rows.push({action:"console",label:"Open recovery console",detail:"Use if the normal viewer cannot connect",icon:"󰆍",enabled:true})
    rows.push({action:"diagnostics",label:"Save diagnostic report",detail:"Connection results without credentials",icon:"󰈙",enabled:true})
  } else if (tab === "Backups") {
    rows.push({action:"backup",label:"Create complete backup…",detail:caps.backup ? "Stops Windows temporarily; asks for your Omarchy password" : "Set the Windows setup workspace in plugin settings",icon:"󰁯",enabled:!!caps.backup})
  }
  return rows
}
