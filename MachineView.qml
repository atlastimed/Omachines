pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Column {
  id: root
  readonly property var popupTokens: Commons.Color.popups
  readonly property var fontTokens: Style.font
  readonly property var spacingTokens: Style.spacing
  property var machine: ({checks:{}, resources:{}, capabilities:{}, operation:{}, backups:[]})
  property var config: ({asideEnabled:true, notify:true, pollSeconds:120})
  property string section: "Overview"
  property int cursorIndex: 0
  property string confirmation: ""
  property var confirmationParameters: ({})
  readonly property var focusedItem: Window.window ? Window.window.activeFocusItem : null
  readonly property bool editing: backupControls.editing || resourceControls.editing || ownsFormFocus(focusedItem)
  function ownsFormFocus(item) { while (item) { if (item === backupControls || item === resourceControls) return true; item=item.parent } return false }
  function focusEditor() { if (section === "Backups") backupControls.focusEntry(); else if (section === "Settings") resourceControls.focusEntry() }
  property string serviceError: ""
  property real clock: Date.now()
  property color foreground: root.popupTokens.text
  property string fontFamily: root.fontTokens.family
  readonly property var sections: ["Overview", "Agents", "Backups", "Settings"]
  readonly property var actions: Model.actionRows(section, machine, config.asideEnabled !== false)
  readonly property bool busy: Model.active(machine)
  readonly property var op: machine.operation || ({})
  signal actionRequested(string action, bool confirmed, var parameters)
  signal settingsChanged(var values)
  signal revealItem(var item)
  signal confirmationClosed()
  onFocusedItemChanged: if (focusedItem && ownsFormFocus(focusedItem)) root.revealItem(focusedItem)
  spacing: Style.space(12)
  onSectionChanged: { cursorIndex = 0; confirmation = "" }
  onCursorIndexChanged: Qt.callLater(function() { if (actionList.itemAt(cursorIndex)) root.revealItem(actionList.itemAt(cursorIndex)) })
  onConfirmationChanged: {
    if (confirmation) Qt.callLater(function() {
      if (root.confirmation && !root.busy) { cancelButton.forceActiveFocus(); root.revealItem(cancelButton) }
    })
    else { cancelButton.focus = false; confirmButton.focus = false; confirmationClosed() }
  }
  onMachineChanged: {
    var count = section === "Settings" ? 3 : actions.length
    if (cursorIndex >= count) cursorIndex = Math.max(0, count - 1)
    if (machine.demo) confirmation = ""
  }
  onBusyChanged: if (busy) confirmation = ""

  function reset() { confirmation = ""; cursorIndex = 0; backupControls.resetDraft(); resourceControls.resetDraft() }
  function moveCursor(dx, dy) {
    if (confirmation !== "") return
    if (dx) section = sections[(sections.indexOf(section) + dx + sections.length) % sections.length]
    var count = section === "Settings" ? 3 : actions.length
    if (dy && count) cursorIndex = (cursorIndex + dy + count) % count
  }
  function activate() {
    // Confirmations deliberately require their explicit button; Enter cannot
    // accidentally confirm a destructive operation after opening its prompt.
    if (confirmation || busy) return
    if (section === "Settings") {
      if (cursorIndex === 0) settingsChanged({asideEnabled: config.asideEnabled === false})
      else if (cursorIndex === 1) settingsChanged({notify: config.notify === false})
      else { let choices=[60,120,300]; settingsChanged({pollSeconds:choices[(choices.indexOf(Number(config.pollSeconds || 120))+1)%3]}) }
      return
    }
    if (!actions.length) return
    choose(actions[cursorIndex])
  }
  function choose(row) {
    if (!row.enabled || busy || machine.demo) return
    if (["stop", "backup", "handoff", "allocate", "rehearse", "restore", "rollback", "rehearsal-pass", "rehearsal-fail"].indexOf(row.action) >= 0) { confirmationParameters=JSON.parse(JSON.stringify(row.parameters || {})); confirmation = row.action }
    else actionRequested(row.action, false, row.parameters || {})
  }
  function textKey(key) {
    if (key === "r") actionRequested("refresh", false, {})
    if (["1","2","3","4"].indexOf(key) >= 0) section = sections[Number(key) - 1]
  }

  PanelHero {
    width: parent.width
    title: root.machine.name || "Windows 11"
    meta: Model.resourceLine(root.machine)
    foreground: root.foreground
    fontFamily: root.fontFamily
    iconComponent: Component { Text { text: "󰍹"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: root.fontTokens.display } }
  }
  ButtonGroup {
    width: parent.width
    options: root.sections.map(function(s) { return {value:s, label:s} })
    value: root.section
    foreground: root.foreground
    fontFamily: root.fontFamily
    fontSize: root.fontTokens.bodySmall
    onChanged: function(value) { root.section = value }
  }
  Text {
    objectName: "summary"
    width: parent.width
    textFormat: Text.PlainText
    text: root.machine.demo ? "Preview data · real actions disabled" : (root.machine.summary || "Checking Windows")
    font.family: root.fontFamily
    font.pixelSize: root.fontTokens.subtitle
    font.bold: true
    color: root.foreground
    wrapMode: Text.WordWrap
  }
  Text {
    width: parent.width
    visible: text !== ""
    textFormat: Text.PlainText
    text: root.serviceError || root.machine.error || ""
    color: Commons.Color.urgent
    font.family: root.fontFamily
    font.pixelSize: root.fontTokens.bodySmall
    wrapMode: Text.WordWrap
  }
  Text {
    width: parent.width
    visible: !!root.op.message
    textFormat: Text.PlainText
    text: (root.busy ? "In progress: " : "") + (root.op.message || "")
    color: root.op.status === "failed" || root.op.status === "interrupted" ? Commons.Color.urgent : root.foreground
    font.family: root.fontFamily
    font.pixelSize: root.fontTokens.bodySmall
    wrapMode: Text.WordWrap
  }

  Column {
    visible: root.section === "Overview"
    width: parent.width
    spacing: Style.space(12)
    StatusRow { width: parent.width; label: "Desktop"; value: Model.checked(root.machine,"desktop").message; foreground: root.foreground; fontFamily: root.fontFamily }
    StatusRow { width: parent.width; label: "Windows disk"; value: Model.freeSpace((root.machine.resources || {}).diskFreeBytes); foreground: root.foreground; fontFamily: root.fontFamily }
    StatusRow { width: parent.width; label: "VM drive"; value: Model.freeSpace((root.machine.storage || {}).availableBytes); foreground: root.foreground; fontFamily: root.fontFamily }
    Text {
      width: parent.width; textFormat: Text.PlainText
      text: "Closing the viewer keeps Windows running. Connection readiness does not indicate whether an agent is working."
      color: Commons.Color.muted; font.family: root.fontFamily; font.pixelSize: root.fontTokens.caption; wrapMode: Text.WordWrap
    }
  }
  Column {
    visible: root.section === "Agents"
    width: parent.width
    spacing: Style.space(12)
    Repeater {
      model: root.config.asideEnabled !== false ? ["ssh","desktop","cua","aside","asideCli","asideAuth"] : ["ssh","desktop","cua"]
      StatusRow {
        required property string modelData
        width: parent.width
        label: ({ssh:"Commands",desktop:"Desktop",cua:"Cua tools",aside:"Aside tools",asideCli:"Aside CLI",asideAuth:"Aside account"})[modelData]
        value: Model.checked(root.machine,modelData).message
        detail: Model.ago(Model.checked(root.machine,modelData).checkedAt,root.clock)
        foreground: root.foreground; fontFamily: root.fontFamily
      }
    }
    Text {
      width: parent.width; textFormat: Text.PlainText
      text: "Agents use the Windows desktop and can access its signed-in accounts. Aside model authentication and usage are not checked here."
      color: Commons.Color.muted; font.family: root.fontFamily; font.pixelSize: root.fontTokens.caption; wrapMode: Text.WordWrap
    }
  }
  Column {
    visible: root.section === "Backups"
    width: parent.width
    spacing: Style.space(12)
    BackupControls {
      id: backupControls
      width:parent.width
      machine:root.machine;foreground:root.foreground;fontFamily:root.fontFamily
      visible:root.confirmation === ""
      onRequest:function(action,parameters){root.choose({action:action,enabled:true,parameters:parameters})}
      onEditingFinished:root.confirmationClosed()
    }
  }

  Column {
    visible: root.section === "Settings"
    width: parent.width
    spacing: Style.space(10)
    Toggle { width: parent.width; hasCursor: root.cursorIndex === 0; label: "Aside integration"; description: "Browser controls and connection checks"; checked: root.config.asideEnabled !== false; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.settingsChanged({asideEnabled: !checked}) }
    Toggle { width: parent.width; hasCursor: root.cursorIndex === 1; label: "Operation notifications"; description: "Backup and lifecycle results"; checked: root.config.notify !== false; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.settingsChanged({notify: !checked}) }
    Text { text: (root.cursorIndex === 2 ? "› " : "") + "Connection check interval"; textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: root.fontTokens.bodySmall }
    ButtonGroup {
      options: [{value:60,label:"1 min"},{value:120,label:"2 min"},{value:300,label:"5 min"}]
      value: Number(root.config.pollSeconds || 120)
      foreground: root.foreground; fontFamily: root.fontFamily
      onChanged: function(value) { root.settingsChanged({pollSeconds:Number(value)}) }
    }
    Text {
      width: parent.width; textFormat: Text.PlainText
      text: "More options are in Omarchy’s bar widget settings. Resource changes require administrator approval and may restart Windows."
      color: Commons.Color.muted; font.family: root.fontFamily; font.pixelSize: root.fontTokens.caption; wrapMode: Text.WordWrap
    }
  }

  ResourceControls {
    id: resourceControls
    width:parent.width
    visible:root.section === "Settings" && root.confirmation === ""
    machine:root.machine;foreground:root.foreground;fontFamily:root.fontFamily
    onRequest:function(action,parameters){root.choose({action:action,enabled:true,parameters:parameters})}
    onEditingFinished:root.confirmationClosed()
  }
  Column {
    width:parent.width
    visible:root.section === "Settings" && root.confirmation === ""
    spacing:root.spacingTokens.controlGap
    Text { text:"Recent operations";color:root.foreground;font.family:root.fontFamily;font.pixelSize:root.fontTokens.subtitle;font.bold:true }
    Text { visible:!(root.machine.history || []).length;text:"No operations recorded yet";color:Commons.Color.muted;font.family:root.fontFamily;font.pixelSize:root.fontTokens.bodySmall }
    Repeater {
      model:root.machine.history || []
      StatusRow {
        required property var modelData
        width:parent.width;foreground:root.foreground;fontFamily:root.fontFamily
        label:modelData.action+" · "+modelData.status
        value:modelData.message || ""
        detail:new Date(modelData.updatedAt).toLocaleString()
      }
    }
  }

  Column {
    width: parent.width
    visible: root.confirmation === ""
    spacing: Style.space(4)
    Repeater {
      id: actionList
      model: root.actions
      CursorSurface {
        id: row
        required property var modelData
        required property int index
        objectName: "action-" + modelData.action
        width: parent.width
        implicitHeight: content.implicitHeight + root.spacingTokens.rowPaddingX * 2
        foreground: root.foreground
        hasCursor: root.cursorIndex === index
        enabled: modelData.enabled && !root.busy && !root.machine.demo
        opacity: enabled ? 1 : 0.5
        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
          onEntered: root.cursorIndex = row.index
          onClicked: root.choose(row.modelData)
        }
        Row {
          id: content
          anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
          anchors.margins: root.spacingTokens.rowPaddingX
          spacing: Style.space(10)
          Text { text: row.modelData.icon; textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: root.fontTokens.icon; anchors.verticalCenter: parent.verticalCenter }
          Column {
            width: parent.width - parent.children[0].width - parent.spacing
            Text { width: parent.width; text: row.modelData.label; textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: root.fontTokens.body; wrapMode: Text.WordWrap }
            Text { width: parent.width; text: row.modelData.detail; textFormat: Text.PlainText; color: Commons.Color.muted; font.family: root.fontFamily; font.pixelSize: root.fontTokens.caption; wrapMode: Text.WordWrap }
          }
        }
      }
    }
  }
  Column {
    visible: root.confirmation !== ""
    enabled: visible && !root.busy && !root.machine.demo
    width: parent.width
    spacing: Style.space(10)
    Text {
      objectName: "confirmation-text"
      width: parent.width; textFormat: Text.PlainText
      text: root.confirmation === "allocate" ? "Apply "+root.confirmationParameters.cpu+" CPUs and "+root.confirmationParameters.ram+" GiB RAM? Running Windows apps will close. Windows restarts if it was running. A terminal asks for your Omarchy password." :
        root.confirmation === "rehearse" ? "Test backup "+root.confirmationParameters.backupId+" in a separate offline VM using 4 GiB RAM? The original Windows VM is preserved. Review the captured boot screen before recording success. Temporary files remain for inspection." :
        root.confirmation === "rollback" ? "Return Windows to the storage retained before restoration? Windows will stop. The terminal asks you to confirm; the replaced storage is also retained." :
        root.confirmation === "restore" ? "Restore backup "+root.confirmationParameters.backupId+"? Windows will stop. The disk, TPM and firmware are replaced with this backup; current storage is retained for recovery. The terminal verifies space and integrity and asks you to type the backup identity before replacement." :
        root.confirmation === "rehearsal-pass" ? "Have you opened the rehearsal image and confirmed that the restored Windows copy reached its desktop or sign-in screen? This records your review and stops only the disposable VM." :
        root.confirmation === "rehearsal-fail" ? "Record that the offline boot failed and stop the disposable VM? Its files remain for inspection." :
        root.confirmation === "backup" ? "Close or save your Windows work first. This will shut down Windows, copy and verify its complete state, then start it again. A terminal asks for your Omarchy password." : root.confirmation === "stop" ? "Shut down Windows? Save your work first. Windows apps and agent tasks will stop." : "Return a disconnected Windows session to its console? This may change where the desktop is displayed. It will not unlock a locked session."
      color: root.foreground; font.family: root.fontFamily; font.pixelSize: root.fontTokens.body; wrapMode: Text.WordWrap
    }
    Row {
      spacing: Style.space(10)
      Button { id: confirmButton; objectName: "confirm-action"; text: "Continue"; bordered: true; focusable: root.confirmation !== ""; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: { if (!root.confirmation || root.busy || root.machine.demo) return; var action = root.confirmation; var parameters=root.confirmationParameters; root.confirmation = ""; root.actionRequested(action,true,parameters) } }
      Button { id: cancelButton; objectName: "cancel-action"; text: "Cancel"; focusable: root.confirmation !== ""; foreground: root.foreground; fontFamily: root.fontFamily; onClicked: root.confirmation = "" }
    }
  }
  Text {
    width: parent.width; textFormat: Text.PlainText
    text: "← → sections · ↑ ↓ actions · Enter select · Tab forms · R check · Esc close"
    color: Commons.Color.muted; font.family: root.fontFamily; font.pixelSize: root.fontTokens.caption; wrapMode: Text.WordWrap
  }
}
