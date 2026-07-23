// Integration guards for the Windows `hermes` resolution wiring in main.ts.
//
// v0.19 extracted the behavior into windows-hermes-path.ts, where real unit
// tests pin extension ordering, updater selection, and probe-before-trust.
// These source assertions ensure main.ts keeps delegating to those helpers and
// still supplies every signal needed to avoid the original reinstall loops.

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'vitest'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function readMain() {
  return fs.readFileSync(path.join(__dirname, 'main.ts'), 'utf8').replace(/\r\n/g, '\n')
}

function functionBody(source: string, name: string) {
  const fnStart = source.indexOf(`function ${name}(`)

  assert.notEqual(fnStart, -1, `${name} must exist in main.ts`)

  const fnEnd = source.indexOf('\nfunction ', fnStart + 1)

  return source.slice(fnStart, fnEnd === -1 ? undefined : fnEnd)
}

test('findOnPath delegates Windows extension ordering to buildPathExtCandidates', () => {
  const source = readMain()
  const body = functionBody(source, 'findOnPath')

  assert.match(
    body,
    /buildPathExtCandidates\(process\.env\.PATHEXT, IS_WINDOWS\)/,
    'findOnPath must use the helper that keeps the empty extension last on Windows'
  )
})

test('Windows bootstrap recovery chooses --update when any real-install signal is present', () => {
  const source = readMain()
  const body = functionBody(source, 'handOffWindowsBootstrapRecovery')

  assert.match(body, /const haveRealInstall =/, 'recovery must compute haveRealInstall')
  assert.match(body, /fileExists\(venvPython\)/, 'recovery must accept the venv interpreter as a real-install signal')
  assert.match(body, /fileExists\(venvHermes\)/, 'recovery must accept the Hermes shim as a real-install signal')
  assert.match(
    body,
    /\.hermes-bootstrap-complete/,
    'recovery must accept the bootstrap-complete marker as a real-install signal'
  )
  assert.match(
    body,
    /chooseUpdaterArgs\(haveRealInstall, branch\)/,
    'recovery must pass the combined install signal to the updater-selection helper'
  )
})

test('unwrapWindowsVenvHermesCommand delegates to the probe-before-trust resolver', () => {
  const source = readMain()
  const body = functionBody(source, 'unwrapWindowsVenvHermesCommand')

  assert.match(
    body,
    /resolveVenvHermesCommand\(command, backendArgs, \{/,
    'unwrap must delegate to the resolver that rejects a broken or partial venv'
  )
  assert.match(
    body,
    /canImportHermesCli,/,
    'unwrap must inject the real runtime import probe into the resolver'
  )
})
