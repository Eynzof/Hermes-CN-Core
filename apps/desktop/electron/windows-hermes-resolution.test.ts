// Integration guards for the Windows `hermes` resolution wiring in main.ts.
//
// v0.19 extracted the behavior into windows-hermes-path.ts, where real unit
// tests pin extension ordering, updater selection, and probe-before-trust.
// Recovery exercises the source-installation resolver with real launchers;
// stale install signals must not bypass its runtime probe.

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { test } from 'vitest'

import { resolveSourceInstallationBackend } from './source-backend'
import { chooseUpdaterArgs } from './windows-hermes-path'

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

test.each([
  { name: 'marker only', marker: true, python: false, shim: false, launcherExit: null, expected: '--repair' },
  {
    name: 'legacy interpreter only',
    marker: false,
    python: true,
    shim: false,
    launcherExit: null,
    expected: '--repair'
  },
  { name: 'legacy shim only', marker: false, python: false, shim: true, launcherExit: null, expected: '--repair' },
  { name: 'stale venv and marker', marker: true, python: true, shim: true, launcherExit: null, expected: '--repair' },
  { name: 'failed PM launcher', marker: true, python: true, shim: true, launcherExit: 1, expected: '--repair' },
  {
    name: 'healthy PM launcher without marker',
    marker: false,
    python: false,
    shim: false,
    launcherExit: 0,
    expected: '--update'
  }
])('Windows bootstrap recovery: $name selects $expected', async ({ marker, python, shim, launcherExit, expected }) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'windows-hermes-recovery-'))
  const isWindows = process.platform === 'win32'
  const venvBin = path.join(root, 'venv', isWindows ? 'Scripts' : 'bin')

  try {
    fs.mkdirSync(path.join(root, 'hermes_cli'))
    fs.writeFileSync(path.join(root, 'hermes_cli', 'main.py'), '')
    fs.mkdirSync(path.join(root, 'pm'))
    fs.mkdirSync(venvBin, { recursive: true })

    if (marker) {
      fs.writeFileSync(path.join(root, '.hermes-bootstrap-complete'), '{}')
    }

    if (python) {
      fs.writeFileSync(path.join(venvBin, isWindows ? 'python.exe' : 'python'), '')
    }

    if (shim) {
      fs.writeFileSync(path.join(venvBin, isWindows ? 'hermes.exe' : 'hermes'), '')
    }

    if (launcherExit !== null) {
      const bin = path.join(root, '.hermes', 'bin')

      const script = isWindows
        ? `@echo off\r\nif not "%~1"=="--version" exit /b 97\r\nexit /b ${launcherExit}\r\n`
        : `#!/bin/sh\n[ "$1" = "--version" ] || exit 97\nexit ${launcherExit}\n`

      fs.mkdirSync(bin, { recursive: true })
      fs.writeFileSync(path.join(bin, isWindows ? 'hermes.cmd' : 'hermes'), script, { mode: 0o755 })
    }

    const backend = await resolveSourceInstallationBackend(root, [], { hermesHome: path.join(root, 'home') })

    assert.deepEqual(chooseUpdaterArgs({ runtimeUsable: backend !== null }, 'release/1.2'), [
      expected,
      '--branch',
      'release/1.2'
    ])
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('unwrapWindowsVenvHermesCommand delegates to the probe-before-trust resolver', () => {
  const source = readMain()
  const body = functionBody(source, 'unwrapWindowsVenvHermesCommand')

  assert.match(
    body,
    /resolveVenvHermesCommand\(command, backendArgs, \{/,
    'unwrap must delegate to the resolver that rejects a broken or partial venv'
  )
  assert.match(body, /canImportHermesCli,/, 'unwrap must inject the real runtime import probe into the resolver')
})
