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

// Minimal stand-ins for the OpenCode v2 plugin SDK. They only mirror the surface the
// plugins use (Plugin.define, Provider/Model ids + defaults, and the tui entrypoint).
function writeHostSdk(installDir) {
  writePackage(
    installDir,
    "@opencode/plugin",
    [
      "export const Plugin = { define: (definition) => definition }",
      "export const Provider = {",
      "  ID: { make: (id) => id },",
      "  Info: { empty: (id) => ({ id, models: new Map() }) },",
      "}",
      "export const Model = {",
      "  ID: { make: (id) => id },",
      "  Info: { default: (providerID, id) => ({ providerID, id }) },",
      "}",
    ].join("\n"),
  )
  const tuiDir = join(installDir, "node_modules", "@opencode", "plugin")
  writeFileSync(
    join(tuiDir, "package.json"),
    JSON.stringify({
      name: "@opencode/plugin",
      version: "2.0.0",
      type: "module",
      exports: { ".": "./index.js", "./tui": "./tui.js" },
    }),
  )
  writeFileSync(join(tuiDir, "tui.js"), "export const Plugin = { define: (definition) => definition }\n")
}

function smokeServer(installDir) {
  writeHostSdk(installDir)
  run(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      [
        'process.env.FREELLMPOOL_TOAST = "0"',
        'const mod = await import("opencode-freellmpool")',
        'if (mod.default?.id !== "freellmpool" || typeof mod.default?.setup !== "function") throw new Error("server plugin did not load")',
        "const tools = new Map()",
        "const providers = new Map()",
        "const models = new Map()",
        "const editor = {",
        "  get: (id) => providers.get(id),",
        "  add: ({ info }) => { providers.set(info.id, { provider: info, models: new Map() }) },",
        "  update: () => {},",
        "  models: { set: (id, list) => models.set(id, list) },",
        "}",
        "const ctx = {",
        "  integration: { transform: async (fn) => fn({ get: () => undefined, update: () => {}, method: { update: () => {} } }) },",
        "  provider: { transform: async (fn) => fn(editor) },",
        "  tool: { transform: async (fn) => fn({ add: (tool) => tools.set(tool.name, tool) }) },",
        "  event: { subscribe: () => (async function* () {})() },",
        "}",
        "const cleanup = await mod.default.setup(ctx)",
        'for (const name of ["freellmpool_status", "freellmpool_models", "freellmpool_tokenmax"]) {',
        '  if (typeof tools.get(name)?.execute !== "function") throw new Error(`server plugin did not register ${name}`)',
        "}",
        'const registered = (models.get("freellmpool") || []).map((model) => model.id)',
        'for (const alias of ["agent", "spread", "auto", "fast", "quality", "fair"]) {',
        '  if (!registered.includes(alias)) throw new Error(`server plugin did not register provider model ${alias}`)',
        "}",
        'if (typeof cleanup !== "function") throw new Error("server plugin setup did not return a cleanup function")',
        "cleanup()",
      ].join("\n"),
    ],
    { cwd: installDir },
  )
}

function smokeTui(installDir) {
  writeHostSdk(installDir)
  writePackage(
    installDir,
    "solid-js",
    [
      "export const createEffect = () => {}",
      "export const createRoot = (fn) => fn(() => {})",
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
        'if (plugin?.id !== "freellmpool-tui" || typeof plugin?.setup !== "function") throw new Error("TUI plugin did not load")',
      ].join(";"),
    ],
    { cwd: installDir },
  )
}

// Stand-in for the registry's @opencode/plugin so the clean install stays offline and
// the tarball's ">=2.0.0" dependency is already satisfied (replaced by writeHostSdk()).
const hostStub = join(work, "host-stub")
mkdirSync(hostStub, { recursive: true })
writeFileSync(
  join(hostStub, "package.json"),
  JSON.stringify({ name: "@opencode/plugin", version: "2.0.0", type: "module", main: "index.js" }),
)
writeFileSync(join(hostStub, "index.js"), "export {}\n")

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
      JSON.stringify({
        name: "freellmpool-package-smoke",
        private: true,
        type: "module",
        dependencies: { "@opencode/plugin": `file:${hostStub}` },
      }),
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
