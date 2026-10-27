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
  property var machine: ({})
  property color foreground: root.popupTokens.text
  property string fontFamily: root.fontTokens.family
  property string backupId: ""
  property bool nameModified: false
  readonly property var records: machine.backups || []
  readonly property var selected: records.filter(function(r) { return root.idFor(r) === root.backupId })[0] || ({})
  readonly property bool editing: selector.popupOpen || nameField.activeFocus
  readonly property var caps: machine.capabilities || ({})
  readonly property string machineName: Model.name(machine)
  signal request(string action, var parameters)
  signal editingFinished()
  function focusEntry() { if (nameField.visible && nameField.enabled) nameField.forceActiveFocus(); return nameField.activeFocus }
  function resetDraft() { nameModified=false; nameField.text=selected.label || "" }
  function idFor(record) { return record.id || String(record.path || "").split("/").pop() }
  onRecordsChanged: if (!records.some(function(r) { return root.idFor(r) === root.backupId })) backupId = records.length ? idFor(records[0]) : ""
  onBackupIdChanged: { nameModified=false; nameField.text=selected.label || "" }
  onSelectedChanged: if (!nameModified && !nameField.activeFocus) nameField.text = selected.label || ""
  spacing: root.spacingTokens.controlGap
  enabled: !Model.active(machine) && !machine.demo
  Dropdown {
    id: selector
    objectName: "backup-selector"
    width: parent.width
    label: "Backup"
    value: root.backupId
    options: root.records.map(function(r) { return {value:root.idFor(r),label:(r.label || "Backup") + " · " + new Date(r.createdAt).toLocaleDateString()} })
    foreground: root.foreground; fontFamily: root.fontFamily
    onChanged: function(value) { root.backupId=value }
    onPopupOpenChanged: if (!popupOpen) root.editingFinished()
  }
  TextField {
    id: nameField; objectName: "backup-name"
    width: parent.width; placeholderText: "Backup name"; maximumLength: 60
    foreground: root.foreground; font.family: root.fontFamily
    enabled: root.backupId !== ""
    onTextEdited: root.nameModified=true
    onAccepted: { root.request("label",{backupId:root.backupId,label:text}); focus=false; root.editingFinished() }
    Keys.onEscapePressed: { focus=false; root.editingFinished() }
  }
  Row {
    spacing: root.spacingTokens.controlGap
    Button { text:"Save name"; focusable:true; foreground:root.foreground; enabled:root.backupId!=="" && nameField.text.trim()!==""; onClicked:root.request("label",{backupId:root.backupId,label:nameField.text}) }
    Button { objectName:"verify-backup"; text:"Verify integrity"; focusable:true; foreground:root.foreground; enabled:root.backupId!=="" && !!root.caps.verify; onClicked:root.request("verify",{backupId:root.backupId}) }
  }
  Text {
    width:parent.width; textFormat:Text.PlainText; wrapMode:Text.WordWrap
    color:root.foreground; font.family:root.fontFamily; font.pixelSize:root.fontTokens.bodySmall
    text: !root.backupId ? (root.caps.catalogue === true ? "Refresh the backup catalogue to find existing backups." : "No backups yet. Create one above.") :
      (root.selected.bytes ? Model.gib(root.selected.bytes)+" · " : "") + new Date(root.selected.createdAt).toLocaleString() + "\n" +
      (root.selected.verified ? (root.selected.verifiedAt ? "Integrity verified" : "Verified by existing setup record") : "Integrity not verified") + (root.selected.verifiedAt ? " · "+new Date(root.selected.verifiedAt).toLocaleString() : "") + "\n" +
      (root.caps.rehearsal === true ? (root.selected.restoreTested ? "Offline restore boot reviewed" : "Restore boot not yet reviewed") :
        (root.selected.restoreTested ? "Restore tested" + (root.selected.restoreTestedAt ? " · " + new Date(root.selected.restoreTestedAt).toLocaleString() : "") : "Restore not yet tested"))
  }
  Button { objectName:"rehearse-backup"; width:parent.width; visible:root.caps.rehearsal === true; text:"Test an offline restored copy…"; bordered:true; focusable:true; foreground:root.foreground; enabled:root.backupId!=="" && !!root.caps.admin; onClicked:root.request("rehearse",{backupId:root.backupId}) }
  Button { objectName:"restore-backup"; width:parent.width; text:"Restore " + root.machineName + " from this backup…"; focusable:true; foreground:root.foreground; enabled:root.backupId!=="" && !!root.caps.restore; onClicked:root.request("restore",{backupId:root.backupId}) }
  Button { width:parent.width; visible:root.caps.rollback === true && ["prepared","restored","needs-attention","rollback-switching"].indexOf((root.machine.recovery || {}).status)>=0; text:"Return to previous Windows storage…"; focusable:true; foreground:root.foreground; onClicked:root.request("rollback",{}) }
  Text {
    width:parent.width; textFormat:Text.PlainText; wrapMode:Text.WordWrap
    color:root.foreground; opacity:0.75; font.family:root.fontFamily; font.pixelSize:root.fontTokens.caption
    text:Model.isOmarchy(root.machine) ? "Restore replaces " + root.machineName + "’s disk and firmware variables, including its files, accounts and installed apps. The current disk is kept aside next to the VM storage. Shared files and CPU/RAM settings are separate." :
      "Restore replaces Windows files, accounts and installed apps with the selected backup. Disk, TPM and firmware are restored together; the previous storage is retained. Current host connections and CPU/RAM settings stay in place. Shared files are separate."
  }
  Column {
    width:parent.width; spacing:root.spacingTokens.controlGap
    visible:root.caps.rehearsal === true && !!(root.machine.rehearsal || {}).status
    Text { width:parent.width; wrapMode:Text.WordWrap; textFormat:Text.PlainText; color:root.foreground; font.family:root.fontFamily; font.pixelSize:root.fontTokens.bodySmall; text:"Offline rehearsal: "+((root.machine.rehearsal || {}).status || "") }
    Button { text:"Open rehearsal image"; focusable:true; foreground:root.foreground; enabled:!!(root.machine.rehearsal || {}).screenshot; onClicked:root.request("rehearsal-image",{}) }
    Row {
      spacing:root.spacingTokens.controlGap
      enabled:["starting","awaiting-review"].indexOf((root.machine.rehearsal || {}).status)>=0
      Button { text:"Windows booted…"; enabled:(root.machine.rehearsal || {}).status === "awaiting-review"; focusable:true; foreground:root.foreground; onClicked:root.request("rehearsal-pass",{}) }
      Button { text:"Boot failed…"; focusable:true; foreground:root.foreground; onClicked:root.request("rehearsal-fail",{}) }
    }
  }
}
