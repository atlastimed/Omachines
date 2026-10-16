pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons

RowLayout {
  id: root
  readonly property var popupTokens: Color.popups
  readonly property var fontTokens: Style.font
  readonly property var spacingTokens: Style.spacing
  property string label: ""
  property string value: ""
  property string detail: ""
  property color foreground: root.popupTokens.text
  property string fontFamily: root.fontTokens.family
  spacing: Style.space(12)
  Text {
    textFormat: Text.PlainText
    text: root.label
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: root.fontTokens.bodySmall
    Layout.preferredWidth: Style.space(112)
    Layout.maximumWidth: root.width * 0.36
    wrapMode: Text.WordWrap
  }
  Column {
    Layout.fillWidth: true
    spacing: Style.space(2)
    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: root.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: root.fontTokens.bodySmall
      wrapMode: Text.WordWrap
    }
    Text {
      visible: root.detail !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: root.detail
      color: Color.muted
      font.family: root.fontFamily
      font.pixelSize: root.fontTokens.caption
      wrapMode: Text.WordWrap
    }
  }
}
