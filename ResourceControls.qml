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
  readonly property var limits: machine.limits || ({})
  readonly property int maxCpu: Number(limits.cpu || (machine.host || {}).cpu || 16)
  readonly property int maxRam: Math.max(4,Number(limits.ram || Math.floor(Number((machine.host || {}).memoryBytes || 32*1073741824)/1073741824*0.75)))
  // The fields, confirmation and request all use the draft clamped to the
  // provider's limits; an allocation outside them is shown as a change.
  readonly property int cpuValue: clamp(draftCpu,1,maxCpu)
  readonly property int ramValue: clamp(draftRam,4,maxRam)
  readonly property var current: currentAllocation()
  readonly property bool outOfRange: current.cpu!==clamp(current.cpu,1,maxCpu) || current.ram!==clamp(current.ram,4,maxRam)
  readonly property bool editing: cpu.field.activeFocus || cpu.field.contentItem.activeFocus || ram.field.activeFocus || ram.field.contentItem.activeFocus
  function focusEntry() { if (cpu.field.visible && cpu.field.enabled) cpu.field.forceActiveFocus(); return cpu.field.activeFocus }
  signal request(string action,var parameters)
  signal editingFinished()
  function clamp(value,low,high) { return Math.min(Math.max(value,low),high) }
  function resetDraft() { modified=false; syncDraft() }
  function currentAllocation() {
    var configured=String((machine.allocation || {}).ram || "").match(/^(\d+)G$/i)
    return {cpu:Number((machine.allocation || {}).cpu || (machine.resources || {}).cpu || 4),
      ram:configured ? Number(configured[1]) : Math.round(Number((machine.resources || {}).memoryBytes || 8*1073741824)/1073741824)}
  }
  function syncDraft() { var c=currentAllocation(); draftCpu=c.cpu; draftRam=c.ram }
  onMachineChanged: {
    if (modified && Number((machine.allocation || {}).cpu)===cpuValue && (machine.allocation || {}).ram===ramValue+"G") modified=false
    if (!modified && !editing) syncDraft()
  }
  spacing:root.spacingTokens.controlGap
  enabled:!Model.active(machine) && !machine.demo
  Text { text:Model.name(root.machine)+" resources"; color:root.foreground; font.family:root.fontFamily; font.pixelSize:root.fontTokens.subtitle; font.bold:true }
  Row {
    spacing:root.spacingTokens.panelGap
    NumberField { id:cpu; objectName:"allocation-cpu"; label:"Virtual CPUs"; value:root.cpuValue; from:1; to:root.maxCpu; foreground:root.foreground; fontFamily:root.fontFamily; onModified:function(value){root.draftCpu=value;root.modified=true} }
    NumberField { id:ram; objectName:"allocation-ram"; label:"RAM (GiB)"; value:root.ramValue; from:4; to:root.maxRam; foreground:root.foreground; fontFamily:root.fontFamily; onModified:function(value){root.draftRam=value;root.modified=true} }
  }
  Text {
    objectName:"allocation-note"
    width:parent.width; wrapMode:Text.WordWrap; textFormat:Text.PlainText; visible:root.outOfRange
    color:root.foreground; font.family:root.fontFamily; font.pixelSize:root.fontTokens.caption
    text:"Currently "+root.current.cpu+" CPUs and "+root.current.ram+" GiB RAM, outside the range allowed here. Apply uses the values shown."
  }
  Text {
    width:parent.width; wrapMode:Text.WordWrap; textFormat:Text.PlainText
    color:root.foreground; opacity:0.75; font.family:root.fontFamily; font.pixelSize:root.fontTokens.caption
    text:"Host: "+Model.gib((root.machine.host || {}).memoryBytes)+" RAM · "+Model.freeSpace((root.machine.host || {}).availableBytes)+" now. "+(Model.isOmarchy(root.machine) ? Model.name(root.machine)+" restarts to apply changes if it is running." : "Windows must stop to apply changes.")+" Leave room for Omarchy and other running apps."
  }
  Button { objectName:"apply-allocation"; text:"Apply allocation…"; bordered:true; focusable:true; foreground:root.foreground; enabled:!!(root.machine.capabilities || {}).allocate; onClicked:root.request("allocate",{cpu:root.cpuValue,ram:root.ramValue}) }
}
