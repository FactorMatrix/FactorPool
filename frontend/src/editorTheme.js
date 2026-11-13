import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { EditorView } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'

// PyCharm's classic Darcula palette, mapped to the Python/C++ Lezer syntax tags.
// https://www.jetbrains.com/help/pycharm/configuring-colors-and-fonts.html
const surface = EditorView.theme({
  '&': { color: '#a9b7c6', backgroundColor: '#2b2b2b', colorScheme: 'dark' },
  '.cm-content': { caretColor: '#bbbbbb' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: '#bbbbbb' },
  '.cm-scroller': { scrollbarColor: '#555555 #2b2b2b' },
  '.cm-scroller::-webkit-scrollbar-thumb': { backgroundColor: '#555555' },
  '.cm-scroller::-webkit-scrollbar-thumb:hover': { backgroundColor: '#686868' },
  '.cm-gutters': { color: '#808080', backgroundColor: '#313335', border: 'none' },
  '.cm-activeLineGutter': { color: '#a9b7c6', backgroundColor: '#323232' },
  // drawSelection paints below the text. Content highlights must let it show through.
  '.cm-activeLine': { backgroundColor: 'rgba(255, 255, 255, 0.035)' },
  '.cm-selectionBackground': { backgroundColor: '#3b4c66' },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground': { backgroundColor: '#264f8f' },
  '.cm-content::selection, .cm-content ::selection': { backgroundColor: '#264f8f' },
  '.cm-selectionMatch': { backgroundColor: 'rgba(80, 159, 100, 0.35)' },
  '.cm-searchMatch': { backgroundColor: 'rgba(80, 159, 100, 0.35)', outline: '1px solid #65875b' },
  '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'rgba(110, 174, 156, 0.25)', outline: '1px solid #ffc66d' },
  '&.cm-focused .cm-matchingBracket': { backgroundColor: 'rgba(110, 174, 156, 0.25)', outline: '1px solid #808080' },
  '&.cm-focused .cm-nonmatchingBracket': { outline: '1px solid #bc3f3c' },
  '.cm-foldPlaceholder': { color: '#a9b7c6', backgroundColor: '#3c3f41', borderColor: '#555555' },
  '.cm-tooltip': { color: '#a9b7c6', backgroundColor: '#3c3f41', border: '1px solid #555555' },
  '.cm-tooltip .cm-tooltip-arrow:before': { borderTopColor: '#555555', borderBottomColor: '#555555' },
  '.cm-tooltip .cm-tooltip-arrow:after': { borderTopColor: '#3c3f41', borderBottomColor: '#3c3f41' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': { color: '#a9b7c6', backgroundColor: '#214283' },
}, { dark: true })

const highlighting = HighlightStyle.define([
  { tag: [t.keyword, t.bool, t.null, t.atom, t.self], color: '#cc7832' },
  { tag: [t.name, t.variableName, t.typeName, t.className, t.namespace], color: '#a9b7c6' },
  { tag: [t.propertyName, t.constant(t.name)], color: '#9876aa' },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: '#a9b7c6' },
  { tag: [t.function(t.definition(t.variableName)), t.function(t.definition(t.propertyName))], color: '#ffc66d' },
  { tag: [t.standard(t.typeName), t.special(t.variableName)], color: '#cc7832' },
  { tag: t.number, color: '#6897bb' },
  { tag: [t.string, t.character, t.regexp], color: '#6a8759' },
  { tag: t.escape, color: '#cc7832' },
  { tag: t.comment, color: '#808080' },
  { tag: t.meta, color: '#bbb529' },
  { tag: [t.operator, t.punctuation], color: '#a9b7c6' },
  { tag: t.separator, color: '#cc7832' },
  { tag: t.invalid, color: '#bc3f3c' },
])

export const darculaTheme = [surface, syntaxHighlighting(highlighting)]
