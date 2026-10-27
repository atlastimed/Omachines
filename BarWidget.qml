pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  readonly property var popupTokens: Commons.Color.popups
  readonly property var fontTokens: Style.font
  readonly property var spacingTokens: Style.spacing
  moduleName: "atlastimed.omachines"
  manageIpc: false
  readonly property var hostBar: bar
  property var machineService: null
  readonly property bool vertical: hostBar ? hostBar.vertical : false
  readonly property var snapshot: machineService ? machineService.snapshot : ({state:"checking",summary:"Connecting to Omachines",checks:{},capabilities:{},backups:[],machines:[]})
  readonly property var config: ({pollSeconds:Number(setting("pollSeconds",120)),notify:flag("notify",true),asideEnabled:flag("asideEnabled",true),setupWorkspace:String(setting("setupWorkspace","")),demo:flag("demo",false)})
  readonly property color foreground: root.popupTokens.text
  readonly property string fontFamily: hostBar ? hostBar.fontFamily : root.fontTokens.family
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function flag(key, fallback) { var value = setting(key,fallback); return typeof value === "string" ? !/^(false|0|no|off)$/i.test(value) : !!value }
  function resolveService() { var api = hostBar ? hostBar.shell : null; if (api && typeof api.serviceFor === "function") machineService = api.serviceFor(moduleName) || null }
  function configure() { if (machineService) machineService.configure(config) }
  function persist(values) {
    var entry = {id:moduleName}
    for (let key in settings) if (key !== "id") entry[key] = settings[key]
    for (let changed in values) entry[changed] = values[changed]
    settings = entry
    if (hostBar && hostBar.shell && typeof hostBar.shell.updateEntryInline === "function") hostBar.shell.updateEntryInline(moduleName,entry)
  }
  function refresh() { if (machineService) machineService.refresh() }
  onBarChanged: resolveService()
  onMachineServiceChanged: configure()
  onConfigChanged: configure()
  onOpenedChanged: if (opened) { view.reset(); view.clock = Date.now(); clock.restart(); Qt.callLater(function() { keys.forceActiveFocus() }) } else clock.stop()
  Timer { interval: 500; running: !root.machineService; repeat: true; triggeredOnStart: true; onTriggered: root.resolveService() }
  Timer { id: clock; interval: 15000; repeat: true; onTriggered: view.clock = Date.now() }

  WidgetButton {
    id: button
    bar: root.bar
    // Shows a glyph for each running VM; the tooltip names every machine.
    text: Model.barText(root.snapshot, root.vertical, root.flag("showLabel",true))
    tooltipText: "Omachines · " + Model.tooltip(root.snapshot)
    onPressed: function(code) {
      if (code === Qt.LeftButton) root.toggle()
      else if (code === Qt.MiddleButton) root.refresh()
      else if (code === Qt.RightButton && root.machineService) root.machineService.action("open",false,{},root.snapshot.machineId || "")
    }
  }
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(view.implicitHeight,Style.space(720))
    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      // Native buttons keep Tab/Enter while a confirmation has focus.
      blocked: view.confirmation !== "" || view.editing
      onCloseRequested: root.close()
      onMoveRequested: function(dx,dy) { view.moveCursor(dx,dy) }
      onActivateRequested: view.activate()
      onTextKey: function(key) { view.textKey(key) }
      // Tab enters a usable form; a disabled or empty one leaves these keys active.
      onTabRequested: function(direction) { if (view.section !== "Backups" && view.section !== "Settings") root.switchPanel(direction); else if (!view.focusEditor()) keys.forceActiveFocus() }
      Keys.onEscapePressed: { if (view.confirmation) view.confirmation = ""; else root.close() }
      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: view.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        clip: true
        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
        MachineView {
          id: view
          width: flick.width
          machine: root.snapshot
          config: root.config
          foreground: root.foreground
          fontFamily: root.fontFamily
          serviceError: root.machineService ? root.machineService.serviceError : ""
          onConfirmationClosed: if (root.opened) keys.forceActiveFocus()
          onActionRequested: function(action,confirmed,parameters,machineId) { if (action === "refresh") root.refresh(); else if (root.machineService) root.machineService.action(action,confirmed,parameters,machineId) }
          onMachineSelected: function(machineId) { if (root.machineService) root.machineService.select(machineId) }
          onSettingsChanged: function(values) { root.persist(values) }
          onSectionChanged: flick.contentY = 0
          onRevealItem: function(item) {
            var point = item.mapToItem(view, 0, 0)
            if (point.y < flick.contentY) flick.contentY = point.y
            else if (point.y + item.height > flick.contentY + flick.height) flick.contentY = Math.min(flick.contentHeight-flick.height, point.y+item.height-flick.height)
          }
        }
      }
    }
  }
}
