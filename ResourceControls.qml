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
  property int draftCpu: 4
  property int draftRam: 8
  property bool modified: false
  readonly property bool editing: cpu.field.activeFocus || cpu.field.contentItem.activeFocus || ram.field.activeFocus || ram.field.contentItem.activeFocus
  function focusEntry() { cpu.field.forceActiveFocus() }
  signal request(string action,var parameters)
  signal editingFinished()
  function resetDraft() { modified=false; syncDraft() }
  function syncDraft() {
    draftCpu=Number((machine.allocation || {}).cpu || (machine.resources || {}).cpu || 4)
    var configured=String((machine.allocation || {}).ram || "").match(/^(\d+)G$/i)
    draftRam=configured ? Number(configured[1]) : Math.round(Number((machine.resources || {}).memoryBytes || 8*1073741824)/1073741824)
  }
  onMachineChanged: {
    if (modified && Number((machine.allocation || {}).cpu)===draftCpu && (machine.allocation || {}).ram===draftRam+"G") modified=false
    if (!modified && !editing) syncDraft()
  }
  spacing:root.spacingTokens.controlGap
  enabled:!Model.active(machine) && !machine.demo
  Text { text:"Windows resources"; color:root.foreground; font.family:root.fontFamily; font.pixelSize:root.fontTokens.subtitle; font.bold:true }
  Row {
    spacing:root.spacingTokens.panelGap
    NumberField { id:cpu; objectName:"allocation-cpu"; label:"Virtual CPUs"; value:root.draftCpu; from:1; to:Number((root.machine.host || {}).cpu || 16); foreground:root.foreground; fontFamily:root.fontFamily; onModified:function(value){root.draftCpu=value;root.modified=true} }
    NumberField { id:ram; objectName:"allocation-ram"; label:"RAM (GiB)"; value:root.draftRam; from:4; to:Math.max(4,Math.floor(Number((root.machine.host || {}).memoryBytes || 32*1073741824)/1073741824*0.75)); foreground:root.foreground; fontFamily:root.fontFamily; onModified:function(value){root.draftRam=value;root.modified=true} }
  }
  Text {
    width:parent.width; wrapMode:Text.WordWrap; textFormat:Text.PlainText
    color:root.foreground; opacity:0.75; font.family:root.fontFamily; font.pixelSize:root.fontTokens.caption
    text:"Host: "+Model.gib((root.machine.host || {}).memoryBytes)+" RAM · "+Model.freeSpace((root.machine.host || {}).availableBytes)+" now. Windows must stop to apply changes. Leave room for Omarchy and other running apps."
  }
  Button { text:"Apply allocation…"; bordered:true; focusable:true; foreground:root.foreground; enabled:!!(root.machine.capabilities || {}).admin; onClicked:root.request("allocate",{cpu:root.draftCpu,ram:root.draftRam}) }
}
