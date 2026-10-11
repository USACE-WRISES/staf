// The markup the Shiny tools render, written as py-htmltools 0.7 writes it.
//
// The tools build their pages with htmltools' tags (ui.div, ui.span, ...) and Shiny inserts the text
// htmltools renders. Whitespace between inline elements shows on the page, so the static app renders
// its copies of that markup with the same rules: a block tag puts each child on its own indented
// line, an inline tag (span, b, button, select, ... : htmltools' _add_ws=False tags) keeps its
// children on one line, and attribute values are escaped as htmltools escapes them. tag() takes its
// arguments as htmltools does: objects are attributes (in order; a repeated name joins with a space),
// anything else is a child (null, undefined and false are dropped, arrays and tag lists flattened).
// An ``_add_ws`` attribute sets the whitespace rule, as htmltools' ``_add_ws=`` does.

/** Raw markup, written as is (htmltools.HTML). */
export class HTML {
  constructor(readonly html: string) {}
}

export type AttrValue = string | number | boolean | null | undefined | HTML
export type Attrs = Record<string, AttrValue>
export type Child = Tag | TagList | HTML | string | null | undefined | false | Child[]

/** htmltools' tags whose _add_ws defaults to False (tags.py). */
const INLINE = new Set(['a', 'abbr', 'audio', 'b', 'bdi', 'bdo', 'br', 'button', 'canvas', 'cite', 'code', 'data',
  'datalist', 'dfn', 'em', 'embed', 'i', 'iframe', 'img', 'input', 'ins', 'kbd', 'label', 'map', 'mark', 'meter',
  'noscript', 'object', 'output', 'picture', 'pre', 'progress', 'q', 'ruby', 's', 'samp', 'select', 'slot', 'small',
  'span', 'strong', 'sub', 'sup', 'svg', 'template', 'textarea', 'time', 'u', 'var', 'video', 'wbr'])
const VOID = new Set(['area', 'base', 'br', 'col', 'command', 'embed', 'hr', 'img', 'input', 'keygen', 'link', 'meta',
  'param', 'source', 'track', 'wbr'])
const NO_ESCAPE = new Set(['script', 'style'])

const TEXT_ESCAPE: [string, string][] = [['&', '&amp;'], ['>', '&gt;'], ['<', '&lt;']]
const ATTR_ESCAPE: [string, string][] = [...TEXT_ESCAPE, ['"', '&quot;'], ["'", '&apos;'], ['\r', '&#13;'], ['\n', '&#10;']]

/** htmltools html_escape. */
export function escapeHtml(text: string, attr = false): string {
  for (const [k, v] of attr ? ATTR_ESCAPE : TEXT_ESCAPE) text = text.split(k).join(v)
  return text
}

/** Python's html.escape (quote=True), for markup the tools build as text. */
export function pyHtmlEscape(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;')
}

type Node = Tag | HTML | string

function flatten(children: Child[], out: Node[] = []): Node[] {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue
    if (Array.isArray(c)) flatten(c, out)
    else if (c instanceof TagList) out.push(...c.children)
    else out.push(c)
  }
  return out
}

/** An attribute's text (a number as an int's str(): pass a float's text as a string). */
function attrValue(v: AttrValue): string | HTML | null {
  if (v === null || v === undefined || v === false) return null
  if (v === true) return ''
  if (typeof v === 'number') return String(v)
  return v
}

export class Tag {
  readonly attrs: [string, string | HTML][] = []
  readonly children: Node[]
  constructor(readonly name: string, args: (Attrs | Child)[], public addWs = !INLINE.has(name)) {
    const kids: Child[] = []
    for (const a of args) {
      if (a !== null && typeof a === 'object' && !Array.isArray(a) && !(a instanceof Tag) && !(a instanceof TagList) && !(a instanceof HTML)) {
        this.setAttrs(a as Attrs)
      } else {
        kids.push(a as Child)
      }
    }
    this.children = flatten(kids)
  }

  private setAttrs(attrs: Attrs): void {
    for (const [k, raw] of Object.entries(attrs)) {
      if (k === '_add_ws') {
        this.addWs = !!raw          // htmltools' _add_ws: keep the children on their own lines, or not
        continue
      }
      const v = attrValue(raw)
      if (v === null) continue
      const name = (k.endsWith('_') ? k.slice(0, -1) : k).replace(/_/g, '-')
      const at = this.attrs.find(([n]) => n === name)
      if (at) at[1] = `${at[1] instanceof HTML ? at[1].html : at[1]} ${v instanceof HTML ? v.html : v}`
      else this.attrs.push([name, v])
    }
  }

  /** Tag.get_html_string */
  render(indent = 0, eol = '\n'): string {
    const pad = '  '.repeat(indent)
    let html = pad + '<' + this.name
    for (const [k, v] of this.attrs) html += ` ${k}="${v instanceof HTML ? v.html : escapeHtml(v, true)}"`
    if (!this.children.length && VOID.has(this.name)) return html + '/>'
    html += '>'
    const close = `</${this.name}>`
    if (!this.children.length) return html + close
    if (this.children.length === 1 && !(this.children[0] instanceof Tag)) {
      const c = this.children[0]
      return html + (c instanceof HTML ? c.html : NO_ESCAPE.has(this.name) ? c : escapeHtml(c)) + close
    }
    if (this.addWs) html += eol
    html += renderList(this.children, indent + 1, eol, this.addWs, !NO_ESCAPE.has(this.name))
    if (this.addWs) html += eol + pad
    return html + close
  }

  toString(): string {
    return this.render()
  }
}

/** TagList.get_html_string */
function renderList(children: Node[], indent: number, eol: string, addWs: boolean, escape = true): string {
  let html = ''
  let first = true
  let prevWs = addWs
  for (const child of children) {
    const ws = prevWs || (child instanceof Tag && child.addWs)
    if (first) first = false
    else if (ws) html += eol
    if (child instanceof Tag) {
      html += ws ? child.render(indent, eol) : child.render(0, '')
      prevWs = child.addWs
    } else {
      if (prevWs) html += '  '.repeat(indent)
      html += child instanceof HTML ? child.html : escape ? escapeHtml(child) : child
      prevWs = false
    }
  }
  return html
}

export class TagList {
  readonly children: Node[]
  constructor(...children: Child[]) {
    this.children = flatten(children)
  }

  render(indent = 0, eol = '\n'): string {
    return renderList(this.children, indent, eol, true)
  }

  toString(): string {
    return this.render()
  }
}

/** A tag: ``tag('div', {class: 'x'}, child, ...)``, as htmltools' Tag(name, *args). */
export function tag(name: string, ...args: (Attrs | Child)[]): Tag {
  return new Tag(name, args)
}

type Maker = (...args: (Attrs | Child)[]) => Tag
const maker = (name: string): Maker => (...args) => new Tag(name, args)

/** The tags the tools use (htmltools.tags, ui.div, ...). */
export const h = {
  a: maker('a'), b: maker('b'), button: maker('button'), details: maker('details'), div: maker('div'),
  h4: maker('h4'), h5: maker('h5'), img: maker('img'), input: maker('input'), label: maker('label'),
  option: maker('option'), p: maker('p'), select: maker('select'), small: maker('small'), span: maker('span'),
  sup: maker('sup'), summary: maker('summary'), table: maker('table'), tbody: maker('tbody'), td: maker('td'),
  textarea: maker('textarea'), th: maker('th'), thead: maker('thead'), tr: maker('tr'), ul: maker('ul'), li: maker('li'),
}

/** What a Shiny ui output holds once rendered: its content's markup between two line breaks, or
 * nothing for None. */
export function outputHtml(content: Child): string {
  const list = new TagList(content)
  return list.children.length ? '\n' + list.render() + '\n' : ''
}
