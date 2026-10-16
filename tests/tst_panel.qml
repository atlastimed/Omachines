import QtQuick
import QtTest
import qs.Commons
import ".." as App

Window {
  visible: true
  width: 480
  height: 1100
  App.MachineView {
    id: view
    width: 430
    machine: ({state:"running",summary:"Windows is ready",checks:{},resources:{cpu:4,memoryBytes:8589934592},capabilities:{viewer:true,ssh:true,cua:true,aside:true,omarchy:true,backup:true},backups:[],operation:{}})
  }
  SignalSpy { id: actionSpy; target: view; signalName: "actionRequested" }
  SignalSpy { id: settingsSpy; target: view; signalName: "settingsChanged" }
  Timer { running: true; interval: 500; onTriggered: tests.qtest_run() }
  TestCase {
    id: tests
    name: "OmachinesPanel"
    when: true
    onCompletedChanged: if (completed) console.log("OMACHINES_TEST_RESULT", qtest_results.failCount, qtest_results.passCount)
    function cleanup() { if (qtest_results.failed) console.error("FAILED TEST",qtest_results.functionName) }
    function init() { view.section = "Overview"; view.reset(); actionSpy.clear(); settingsSpy.clear() }
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
