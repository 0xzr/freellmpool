#!/usr/bin/env node

import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const work = mkdtempSync(join(tmpdir(), "freellmpool-opencode-packages-"))
const bun = process.env.BUN_BIN || "bun"

const packages = [
  {
    directory: "opencode",
    name: "opencode-freellmpool",
    files: [
      "LICENSE",
      "README.md",
      "freellmpool.js",
      "package.json",
      "plugin/freellmpool.js",
      "tools/freellmpool_models.js",
      "tools/freellmpool_status.js",
      "tools/freellmpool_tokenmax.js",
    ],
  },
  {
    directory: "opencode-tui",
    name: "opencode-freellmpool-tui",
    files: ["LICENSE", "README.md", "index.tsx", "package.json"],
  },
]

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || root,
    encoding: "utf8",
    env: { ...process.env, ...(options.env || {}) },
  })
  if (result.status !== 0) {
    throw new Error(
      [`command failed: ${command} ${args.join(" ")}`, result.stdout, result.stderr]
        .filter(Boolean)
        .join("\n"),
    )
  }
  return result.stdout
}

function writePackage(directory, name, source) {
  const packageDir = join(directory, "node_modules", ...name.split("/"))
  mkdirSync(packageDir, { recursive: true })
  writeFileSync(
    join(packageDir, "package.json"),
    JSON.stringify({ name, version: "0.0.0-smoke", type: "module", exports: "./index.js" }),
  )
  writeFileSync(join(packageDir, "index.js"), source)
}

function smokeServer(installDir) {
  writePackage(
    installDir,
    "@opencode-ai/plugin",
    [
      "const schema = new Proxy(function () { return schema }, {",
      "  get() { return schema },",
      "  apply() { return schema },",
      "})",
      "export const tool = Object.assign((definition) => definition, { schema })",
    ].join("\n"),
  )
  run(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      [
        'const mod = await import("opencode-freellmpool")',
        "const loaded = await mod.default({ client: { tui: { showToast: async () => {} } } })",
        'if (loaded?.tool?.freellmpool_status?.description === undefined) throw new Error("server plugin did not register tools")',
        'const localPlugin = await import("opencode-freellmpool/plugin/freellmpool.js")',
        'if (typeof localPlugin.default !== "function") throw new Error("local plugin shim did not load")',
        'for (const name of ["freellmpool_status", "freellmpool_models", "freellmpool_tokenmax"]) {',
        '  const custom = await import(`opencode-freellmpool/tools/${name}.js`)',
        '  if (custom.default?.description === undefined) throw new Error(`${name} custom tool did not load`)',
        '}',
      ].join(";"),
    ],
    { cwd: installDir },
  )
}

function smokeTui(installDir) {
  writePackage(
    installDir,
    "solid-js",
    [
      "export const createEffect = () => {}",
      "export const createSignal = (value) => [() => value, () => {}]",
      "export const onCleanup = () => {}",
      "export const For = () => null",
      "export const Show = () => null",
    ].join("\n"),
  )
  const openTuiDir = join(installDir, "node_modules", "@opentui", "solid")
  mkdirSync(openTuiDir, { recursive: true })
  writeFileSync(
    join(openTuiDir, "package.json"),
    JSON.stringify({
      name: "@opentui/solid",
      version: "0.0.0-smoke",
      type: "module",
      exports: {
        ".": "./index.js",
        "./jsx-runtime": "./jsx-runtime.js",
        "./jsx-dev-runtime": "./jsx-runtime.js",
      },
    }),
  )
  writeFileSync(join(openTuiDir, "index.js"), "export {}\n")
  writeFileSync(
    join(openTuiDir, "jsx-runtime.js"),
    [
      "export const Fragment = Symbol.for('fragment')",
      "export const jsx = (type, props) => ({ type, props })",
      "export const jsxs = jsx",
      "export const jsxDEV = jsx",
    ].join("\n"),
  )
  run(
    bun,
    [
      "--eval",
      [
        'import plugin from "opencode-freellmpool-tui/tui"',
        'if (plugin?.id !== "freellmpool-tui" || typeof plugin?.tui !== "function") throw new Error("TUI plugin did not load")',
      ].join(";"),
    ],
    { cwd: installDir },
  )
}

try {
  for (const spec of packages) {
    const packageDir = join(root, "integrations", spec.directory)
    // Contract: npm pack --json, followed by a clean npm install --ignore-scripts --omit=peer.
    const packed = JSON.parse(
      run("npm", ["pack", "--json", "--pack-destination", work], { cwd: packageDir }),
    )
    if (!Array.isArray(packed) || packed.length !== 1) {
      throw new Error(`${spec.name}: npm pack returned an unexpected result`)
    }
    const actualFiles = packed[0].files.map((entry) => entry.path).sort()
    const expectedFiles = [...spec.files].sort()
    if (JSON.stringify(actualFiles) !== JSON.stringify(expectedFiles)) {
      throw new Error(
        `${spec.name}: packed files ${JSON.stringify(actualFiles)} != ${JSON.stringify(expectedFiles)}`,
      )
    }
    const tarball = join(work, packed[0].filename)
    const installDir = join(work, `install-${spec.directory}`)
    mkdirSync(installDir)
    writeFileSync(
      join(installDir, "package.json"),
      JSON.stringify({ name: "freellmpool-package-smoke", private: true, type: "module" }),
    )
    run(
      "npm",
      ["install", "--ignore-scripts", "--omit=peer", "--no-audit", "--no-fund", tarball],
      { cwd: installDir },
    )
    const installed = JSON.parse(
      readFileSync(join(installDir, "node_modules", spec.name, "package.json"), "utf8"),
    )
    if (installed.name !== spec.name) throw new Error(`${spec.name}: clean install failed`)
    if (spec.directory === "opencode") smokeServer(installDir)
    else smokeTui(installDir)
    process.stdout.write(`validated ${spec.name}@${installed.version}\n`)
  }
} finally {
  rmSync(work, { recursive: true, force: true })
}
