import QtQuick
import QtTest
import qs.Commons
import qs.Ui
import ".." as App

Window {
  visible: true
  width: 480
  height: 1100
  PanelKeyCatcher {
    id: keys
    anchors.fill: parent
    blocked: view.confirmation !== ""
    onMoveRequested: function(dx,dy) { view.moveCursor(dx,dy) }
    onActivateRequested: view.activate()
    onTextKey: function(key) { view.textKey(key) }
    Keys.onEscapePressed: { if (view.confirmation) view.confirmation = ""; else keys.closeRequested() }
    App.MachineView {
    id: view
    width: 430
    machine: ({state:"running",summary:"Windows is ready",checks:{},resources:{cpu:4,memoryBytes:8589934592},capabilities:{viewer:true,ssh:true,cua:true,aside:true,omarchy:true,backup:true},backups:[],operation:{}})
    onConfirmationClosed: keys.forceActiveFocus()
    }
  }
  SignalSpy { id: actionSpy; target: view; signalName: "actionRequested" }
  SignalSpy { id: settingsSpy; target: view; signalName: "settingsChanged" }
  SignalSpy { id: closeSpy; target: keys; signalName: "closeRequested" }
  Timer { running: true; interval: 500; onTriggered: tests.qtest_run() }
  TestCase {
    id: tests
    name: "OmachinesPanel"
    when: true
    onCompletedChanged: if (completed) console.log("OMACHINES_TEST_RESULT", qtest_results.failCount, qtest_results.passCount)
    function cleanup() { if (qtest_results.failed) console.error("FAILED TEST",qtest_results.functionName) }
    function init() {
      view.machine={state:"running",summary:"Windows is ready",checks:{},resources:{cpu:4,memoryBytes:8589934592},capabilities:{viewer:true,ssh:true,cua:true,aside:true,omarchy:true,backup:true},backups:[],operation:{}}
      view.config={asideEnabled:true,notify:true,pollSeconds:120}
      view.section = "Overview"; view.reset(); keys.forceActiveFocus(); actionSpy.clear(); settingsSpy.clear(); closeSpy.clear()
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
    function test_theme_tokens_react() {
      var old=Color.shellValues
      Color.shellValues={"popups.text":"#111111"};compare(view.foreground.toString(),"#111111")
      Color.shellValues={"popups.text":"#eeeeee"};compare(view.foreground.toString(),"#eeeeee")
      Color.shellValues=old
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
      mouseClick(findChild(view,"cancel-action"));compare(view.confirmation,"");compare(actionSpy.count,0)
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
