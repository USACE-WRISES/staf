// The markup renderer against py-htmltools' own renderings (htm_ref.json, make_htm_fixtures.py).
import { describe, expect, it } from 'vitest'
import { type Child, HTML, tag, TagList } from '../src/app/htm'
import ref from './fixtures/htm_ref.json'

type Tree = string | { html: string } | [string, ...unknown[]]

function build(node: Tree): Child {
  if (typeof node === 'string') return node
  if (!Array.isArray(node)) return new HTML(node.html)
  if (node[0] === 'taglist') return new TagList(...(node.slice(1) as Tree[]).map(build))
  const [name, attrs, ...kids] = node as [string, Record<string, string>, ...Tree[]]
  return tag(name, ...kids.map(build), attrs)
}

describe('htm renders as htmltools does', () => {
  for (const [i, c] of (ref as { tree: Tree, html: string }[]).entries()) {
    it(`case ${i}`, () => {
      expect(new TagList(build(c.tree)).render()).toBe(c.html)
    })
  }
})
