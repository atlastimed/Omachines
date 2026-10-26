import QtQuick
import QtTest
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import ".." as App
import "../Model.js" as Model

Window {
  visible: true
  width: 480
  height: 1100
  PanelKeyCatcher {
    id: keys
    anchors.fill: parent
    blocked: view.confirmation !== "" || view.editing
    onMoveRequested: function(dx,dy) { view.moveCursor(dx,dy) }
    onActivateRequested: view.activate()
    onTextKey: function(key) { view.textKey(key) }
    onTabRequested: view.focusEditor()
    Keys.onEscapePressed: { if (view.confirmation) view.confirmation = ""; else keys.closeRequested() }
    App.MachineView {
    id: view
    width: 430
    machine: ({state:"running",summary:"Windows is ready",checks:{},resources:{cpu:4,memoryBytes:8589934592},capabilities:{viewer:true,ssh:true,cua:true,aside:true,omarchy:true,lifecycle:true,backup:true,shared:true,console:true,handoff:true,asideSupported:true,catalogue:true,rehearsal:true,rollback:true},backups:[],operation:{}})
    onConfirmationClosed: keys.forceActiveFocus()
    }
  }
  SignalSpy { id: actionSpy; target: view; signalName: "actionRequested" }
  SignalSpy { id: settingsSpy; target: view; signalName: "settingsChanged" }
  SignalSpy { id: closeSpy; target: keys; signalName: "closeRequested" }
  SignalSpy { id: selectSpy; target: view; signalName: "machineSelected" }
  Timer { running: true; interval: 500; onTriggered: tests.qtest_run() }
  TestCase {
    id: tests
    name: "OmachinesPanel"
    when: true
    onCompletedChanged: if (completed) console.log("OMACHINES_TEST_RESULT", qtest_results.failCount, qtest_results.passCount)
    function cleanup() { if (qtest_results.failed) console.error("FAILED TEST",qtest_results.functionName) }
    function init() {
      view.machine={state:"running",summary:"Windows is ready",name:"Windows 11",provider:"omarchy-windows",machineId:"windows",checks:{},resources:{cpu:4,memoryBytes:8589934592},capabilities:{viewer:true,ssh:true,cua:true,aside:true,omarchy:true,lifecycle:true,backup:true,shared:true,console:true,handoff:true,asideSupported:true,catalogue:true,rehearsal:true,rollback:true},backups:[],operation:{}}
      view.config={asideEnabled:true,notify:true,pollSeconds:120}
      view.section = "Overview"; view.reset(); keys.forceActiveFocus(); actionSpy.clear(); settingsSpy.clear(); closeSpy.clear(); selectSpy.clear()
    }
    readonly property var both: [{id:"windows",name:"Windows 11",provider:"omarchy-windows",state:"running",active:true,summary:"Windows is ready"},{id:"omarchy",name:"Omarchy VM",provider:"omarchy-qemu",state:"stopped",active:false,summary:"Omarchy VM is stopped"}]
    function omarchyMachine(extra) {
      return Object.assign({state:"running",summary:"Omarchy VM is ready",name:"Omarchy VM",provider:"omarchy-qemu",machineId:"omarchy",checks:{},resources:{cpu:6,memoryBytes:8589934592},
        capabilities:{viewer:true,lifecycle:true,ssh:true,cua:true,shared:true,backup:true,verify:true,restore:true,allocate:true,aside:false,asideSupported:false,console:false,handoff:false,admin:false,catalogue:false,rehearsal:false,rollback:false},
        backups:[{id:"omarchy-20261026T205001Z",label:"Fresh install",createdAt:"2026-10-26T20:50:40Z",verified:true,restoreTested:true}],operation:{},
        machines:[Object.assign({},both[0],{state:"stopped",active:false}),Object.assign({},both[1],{state:"running",active:true})]}, extra || {})
    }
    function chip(label) {
      var switcher=findChild(view,"machine-switcher")
      for (var i=0;i<switcher.children.length;i++) if (String(switcher.children[i].text || "").indexOf(label)===0) return switcher.children[i]
      return null
    }
    function actionNames(section) { view.section=section; return view.actions.map(function(a){return a.action}) }
    function test_switcher_hidden_for_one_machine() {
      view.machine=Object.assign({},view.machine,{machines:[both[0]]})
      verify(!findChild(view,"machine-switcher").visible)
      keyClick(Qt.Key_M);compare(selectSpy.count,0)
      verify(findChild(view,"key-hint").text.indexOf("M machine")<0)
    }
    function test_switcher_click_and_m_key_select_machine() {
      view.machine=Object.assign({},view.machine,{machines:both})
      verify(findChild(view,"machine-switcher").visible)
      verify(findChild(view,"key-hint").text.indexOf("M machine")>=0)
      var target=chip("Omarchy VM");verify(target!==null);compare(target.text,"Omarchy VM · Off")
      wait(30);mouseClick(target);compare(selectSpy.count,1);compare(selectSpy.signalArguments[0][0],"omarchy")
      mouseClick(chip("Windows 11"));compare(selectSpy.count,1)
      tryCompare(keys,"activeFocus",true);keyClick(Qt.Key_M)
      compare(selectSpy.count,2);compare(selectSpy.signalArguments[1][0],"omarchy");compare(actionSpy.count,0)
    }
    function test_switching_machine_clears_confirmation() {
      view.machine=Object.assign({},view.machine,{machines:both})
      openConfirmation();view.machine=omarchyMachine()
      compare(view.confirmation,"");compare(actionSpy.count,0);compare(view.cursorIndex,0)
      activateNextAction()
    }
    function test_omarchy_hides_windows_only_controls() {
      view.machine=omarchyMachine()
      compare(actionNames("Overview"),["open","shared","start","stop"])
      compare(actionNames("Agents"),["refresh","connections","diagnostics"])
      compare(actionNames("Backups"),["backup"])
      verify(!findChild(view,"rehearse-backup").visible)
      compare(findChild(view,"restore-backup").text,"Restore Omarchy VM from this backup…")
      verify(findChild(view,"restore-backup").enabled);verify(findChild(view,"verify-backup").enabled)
      compare(Model.agentChecks(view.machine,true),["ssh","desktop","cua"])
      verify(view.actions.every(function(a){return a.label.indexOf("Windows")<0 && a.detail.indexOf("Windows")<0}))
      view.section="Settings";verify(findChild(view,"apply-allocation").enabled)
    }
    function test_windows_keeps_its_controls() {
      view.machine=Object.assign({},view.machine,{capabilities:Object.assign({},view.machine.capabilities,{admin:true})})
      compare(actionNames("Agents"),["refresh","handoff","console","connections","inspect","diagnostics"])
      verify(actionNames("Overview").indexOf("aside")>=0)
      view.section="Backups";verify(findChild(view,"rehearse-backup").visible)
    }
    function test_omarchy_confirmation_names_machine_and_pins_it() {
      view.machine=omarchyMachine()
      view.choose(view.actions.filter(function(a){return a.action === "stop"})[0])
      tryVerify(function(){return findChild(view,"cancel-action").activeFocus})
      verify(findChild(view,"confirmation-text").text.indexOf("Shut down Omarchy VM?")===0)
      keyClick(Qt.Key_Return);compare(actionSpy.count,0)
      view.section="Backups";view.choose(view.actions[0])
      verify(findChild(view,"confirmation-text").text.indexOf("No password is needed")>=0)
      findChild(view,"confirm-action").forceActiveFocus();keyClick(Qt.Key_Return)
      compare(actionSpy.count,1);compare(actionSpy.signalArguments[0][0],"backup");compare(actionSpy.signalArguments[0][3],"omarchy")
    }
    function test_operation_of_other_machine_is_named() {
      view.machine=omarchyMachine({operation:{status:"running",action:"backup",machineId:"windows",message:"Copying Windows storage"}})
      compare(findChild(view,"operation-message").text,"In progress: Windows 11: Copying Windows storage")
      view.activate();compare(actionSpy.count,0)
      view.machine=omarchyMachine({operation:{status:"complete",action:"stop",machineId:"windows",message:"Windows shut down"}})
      verify(!findChild(view,"operation-message").visible)
      view.machine=omarchyMachine({operation:{status:"failed",action:"backup",machineId:"windows",message:"Backup failed"}})
      verify(findChild(view,"operation-message").visible)
      view.machine=omarchyMachine({operation:{status:"complete",action:"stop",machineId:"omarchy",message:"Omarchy VM shut down"}})
      verify(findChild(view,"operation-message").visible)
    }
    function test_bar_text_shows_each_running_vm() {
      var state={machines:both,operation:{}}
      compare(Model.barText({machines:[]},false,true),"󰍹 VM")
      compare(Model.barText(state,false,true),"󰍹 VM \uf17a")
      state.machines=[both[0],Object.assign({},both[1],{active:true})]
      compare(Model.barText(state,false,true),"󰍹 VM \uf17a \uf303")
      compare(Model.barText(state,true,true),"\uf17a\n\uf303")
      compare(Model.barText({machines:[Object.assign({},both[0],{active:false})],operation:{status:"running"}},false,true),"󰍹 VM…")
      compare(Model.barText({machines:[]},false,false),"󰍹")
      compare(Model.tooltip(state),"Windows 11: Windows is ready · Omarchy VM: Omarchy VM is stopped")
    }
    function openConfirmation() {
      view.choose(view.actions.filter(function(a){return a.action === "stop"})[0])
      tryVerify(function() { return findChild(view,"cancel-action").activeFocus })
      compare(actionSpy.count,0)
    }
    function activateNextAction() {
      tryCompare(keys,"activeFocus",true)
      keyClick(Qt.Key_Right);compare(view.section,"Agents")
      var before=actionSpy.count
      keyClick(Qt.Key_Return)
      compare(actionSpy.count,before+1);compare(actionSpy.signalArguments[before][0],"refresh")
    }
    function test_real_keys_after_cancel() {
      openConfirmation();keyClick(Qt.Key_Return)
      compare(view.confirmation,"");compare(actionSpy.count,0)
      activateNextAction()
    }
    function test_real_keys_after_continue() {
      openConfirmation();findChild(view,"confirm-action").forceActiveFocus();keyClick(Qt.Key_Return)
      compare(view.confirmation,"");compare(actionSpy.count,1)
      compare(actionSpy.signalArguments[0][0],"stop");compare(actionSpy.signalArguments[0][1],true)
      activateNextAction()
    }
    function test_real_keys_after_escape() {
      openConfirmation();keyClick(Qt.Key_Escape)
      compare(view.confirmation,"");compare(actionSpy.count,0)
      compare(closeSpy.count,0)
      activateNextAction()
      keyClick(Qt.Key_Escape);compare(closeSpy.count,1)
    }
    function test_real_keys_after_automatic_dismissal() {
      var original=view.machine
      openConfirmation();view.machine=Object.assign({},original,{operation:{status:"running"}})
      compare(view.confirmation,"");compare(actionSpy.count,0)
      view.machine=original;activateNextAction()
    }
    function test_queued_confirmation_focus_does_not_steal_focus_after_close() {
      view.choose(view.actions.filter(function(a){return a.action === "stop"})[0]);view.reset()
      wait(30);activateNextAction()
    }
    function test_backup_name_editor_keeps_shortcuts_as_text() {
      view.machine=Object.assign({},view.machine,{capabilities:{admin:true},backups:[{id:"omachines-20260101T000000Z-abcdef",label:"Saved Windows",createdAt:"2026-01-01T00:00:00Z"}]})
      view.section="Backups";keyClick(Qt.Key_Tab)
      var field=findChild(view,"backup-name")
      tryCompare(field,"activeFocus",true);verify(view.editing)
      field.selectAll();keyClick(Qt.Key_R);keyClick(Qt.Key_1);keyClick(Qt.Key_4)
      compare(field.text,"r14");compare(view.section,"Backups");compare(actionSpy.count,0)
      keyClick(Qt.Key_Return)
      compare(actionSpy.count,1);compare(actionSpy.signalArguments[0][0],"label")
      compare(actionSpy.signalArguments[0][2].label,"r14")
      tryCompare(keys,"activeFocus",true)
    }
    function test_resource_editor_keeps_number_keys() {
      view.machine=Object.assign({},view.machine,{capabilities:{admin:true},host:{cpu:16,memoryBytes:34359738368},allocation:{cpu:4,ram:"8G"}})
      view.section="Settings";keyClick(Qt.Key_Tab)
      var field=findChild(view,"allocation-cpu").field
      tryVerify(function(){return view.editing})
      field.contentItem.forceActiveFocus();field.contentItem.selectAll();keyClick(Qt.Key_6);keyClick(Qt.Key_Tab)
      compare(view.section,"Settings");compare(field.value,6);compare(actionSpy.count,0)
    }
    function test_restore_confirmation_pins_selected_backup() {
      var parameters={backupId:"omachines-20260101T000000Z-abcdef"}
      view.choose({enabled:true,action:"restore",parameters:parameters})
      parameters.backupId="omachines-20260102T000000Z-abcdef"
      tryVerify(function(){return findChild(view,"cancel-action").activeFocus})
      findChild(view,"confirm-action").forceActiveFocus();keyClick(Qt.Key_Return)
      compare(actionSpy.count,1)
      compare(actionSpy.signalArguments[0][2].backupId,"omachines-20260101T000000Z-abcdef")
    }
    function test_resource_confirmation_is_cancel_first_and_busy_safe() {
      view.choose({enabled:true,action:"allocate",parameters:{cpu:6,ram:8}})
      tryVerify(function(){return findChild(view,"cancel-action").activeFocus})
      keyClick(Qt.Key_Return);compare(actionSpy.count,0)
      view.choose({enabled:true,action:"allocate",parameters:{cpu:6,ram:8}})
      view.machine=Object.assign({},view.machine,{operation:{status:"running"}})
      compare(view.confirmation,"");compare(actionSpy.count,0)
    }
    function test_theme_tokens_react() {
      var old=Commons.Color.shellValues
      Commons.Color.shellValues={"popups.text":"#111111"};compare(view.foreground.toString(),"#111111")
      Commons.Color.shellValues={"popups.text":"#eeeeee"};compare(view.foreground.toString(),"#eeeeee")
      Commons.Color.shellValues=old
    }
    function test_sections_and_keyboard_cursor() {
      view.moveCursor(1,0); compare(view.section,"Agents")
      view.moveCursor(-1,0); compare(view.section,"Overview")
      view.moveCursor(0,1); compare(view.cursorIndex,1)
      view.textKey("3"); compare(view.section,"Backups")
    }
    function test_shutdown_requires_explicit_confirmation() {
      var action = view.actions.filter(function(a){return a.action === "stop"})[0]
      view.choose(action)
      compare(view.confirmation,"stop"); compare(actionSpy.count,0)
      view.activate(); compare(actionSpy.count,0)
      var confirm=findChild(view,"confirm-action")
      verify(confirm!==null); wait(30); mouseClick(confirm)
      compare(actionSpy.count,1); compare(actionSpy.signalArguments[0][0],"stop");compare(actionSpy.signalArguments[0][1],true)
    }
    function test_cancel_preserves_machine() {
      view.section="Backups";view.choose(view.actions[0]);compare(view.confirmation,"backup")
      wait(30);mouseClick(findChild(view,"cancel-action"));compare(view.confirmation,"");compare(actionSpy.count,0)
    }
    function test_demo_disables_actions() {
      var original=view.machine;view.machine=Object.assign({},original,{demo:true})
      view.choose(view.actions[0]);compare(actionSpy.count,0)
      view.machine=original
    }
    function test_busy_disables_actions() {
      var original=view.machine;view.machine=Object.assign({},original,{operation:{status:"running"}})
      view.activate();compare(actionSpy.count,0)
      view.machine=original
    }
    function test_optional_aside() {
      view.config={asideEnabled:false};compare(view.actions.filter(function(a){return a.action==="aside"}).length,0)
      view.config={asideEnabled:true}
    }
    function test_settings_keyboard_and_signal() {
      view.config={asideEnabled:true,notify:true,pollSeconds:120};view.section="Settings"
      view.activate();compare(settingsSpy.count,1);compare(settingsSpy.signalArguments[0][0].asideEnabled,false)
      view.moveCursor(0,2);view.activate();compare(settingsSpy.signalArguments[1][0].pollSeconds,300)
      view.machine=Object.assign({},view.machine,{checkedAt:"new"});compare(view.cursorIndex,2)
    }
  }
}
