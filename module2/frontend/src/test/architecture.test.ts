// @vitest-environment node
//
// Guards the services-layer boundary: every backend call goes through
// WaitWiseService, and nothing outside src/services reaches for the network or
// the mock. Assertions walk the parsed AST, never raw source text, so a comment
// that mentions fetch can neither break nor satisfy them.

import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseAst } from 'rolldown/parseAst'
import { describe, expect, it } from 'vitest'

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const NETWORK_GLOBALS = new Set(['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource'])

interface SourceFile {
  path: string // relative to src/, forward slashes
  imports: string[] // resolved to src-relative paths when local
  networkRefs: string[]
}

function listSourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) return listSourceFiles(full)
    return /\.tsx?$/.test(entry.name) && !entry.name.endsWith('.d.ts') ? [full] : []
  })
}

function toSrcPath(absolute: string): string {
  return relative(SRC, absolute).split(sep).join('/')
}

type Node = { type: string; [key: string]: unknown }

function walk(node: unknown, visit: (node: Node) => void): void {
  if (Array.isArray(node)) {
    node.forEach((child) => walk(child, visit))
  } else if (node && typeof node === 'object' && typeof (node as Node).type === 'string') {
    visit(node as Node)
    for (const value of Object.values(node)) walk(value, visit)
  }
}

function analyze(absolute: string): SourceFile {
  const lang = absolute.endsWith('.tsx') ? 'tsx' : 'ts'
  const program = parseAst(readFileSync(absolute, 'utf8'), { lang }, absolute)
  const imports: string[] = []
  const networkRefs: string[] = []

  const addImport = (source: unknown) => {
    if (typeof source !== 'string') return
    imports.push(source.startsWith('.') ? toSrcPath(resolve(dirname(absolute), source)) : source)
  }

  walk(program, (node) => {
    if (node.type === 'ImportDeclaration' || node.type === 'ExportNamedDeclaration' || node.type === 'ExportAllDeclaration') {
      addImport((node.source as { value?: unknown } | null)?.value)
    }
    if (node.type === 'ImportExpression') addImport((node.source as { value?: unknown }).value)
    if (node.type === 'Identifier' && NETWORK_GLOBALS.has(node.name as string)) {
      networkRefs.push(node.name as string)
    }
  })

  return { path: toSrcPath(absolute), imports, networkRefs }
}

const files = listSourceFiles(SRC).map(analyze)
const isTest = (f: SourceFile) => /\.test\.tsx?$/.test(f.path) || f.path.startsWith('test/')
const appFiles = files.filter((f) => !isTest(f))

describe('services layer boundary', () => {
  it('finds the source files it is guarding', () => {
    expect(appFiles.map((f) => f.path)).toEqual(
      expect.arrayContaining([
        'pages/HomePage.tsx',
        'services/index.ts',
        'services/mock/mockService.ts',
        'services/http/httpService.ts',
      ]),
    )
  })

  it('makes network calls only inside src/services', () => {
    const offenders = appFiles
      .filter((f) => !f.path.startsWith('services/') && f.networkRefs.length > 0)
      .map((f) => `${f.path}: ${f.networkRefs.join(', ')}`)
    expect(offenders).toEqual([])
  })

  it('makes network calls only in the HTTP implementation, never in the mock', () => {
    const offenders = appFiles
      .filter((f) => f.path.startsWith('services/') && !f.path.startsWith('services/http/'))
      .filter((f) => f.networkRefs.length > 0)
      .map((f) => f.path)
    expect(offenders).toEqual([])
  })

  it.each(['mock', 'http'])('imports the %s implementation only from services/index.ts', (impl) => {
    const offenders = appFiles
      .filter((f) => !f.path.startsWith(`services/${impl}/`) && f.path !== 'services/index.ts')
      .filter((f) => f.imports.some((i) => i.startsWith(`services/${impl}/`)))
      .map((f) => f.path)
    expect(offenders).toEqual([])
  })

  it('keeps pages and components off HTTP client libraries', () => {
    const offenders = appFiles
      .filter((f) => f.path.startsWith('pages/') || f.path.startsWith('components/'))
      .filter((f) => f.imports.some((i) => ['axios', 'ky', 'ofetch'].includes(i)))
      .map((f) => f.path)
    expect(offenders).toEqual([])
  })

  it('keeps business rules in src/domain free of React and the services layer', () => {
    const offenders = appFiles
      .filter((f) => f.path.startsWith('domain/'))
      .filter((f) => f.imports.some((i) => i === 'react' || i.startsWith('services/')))
      .map((f) => f.path)
    expect(offenders).toEqual([])
  })
})
