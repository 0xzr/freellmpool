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
  const script = `
process.env.FREELLMPOOL_TOAST = "0"
process.env.FREELLMPOOL_MODELS_DISCOVERY = "1"
const mod = await import("opencode-freellmpool")
if (mod.default?.id !== "freellmpool" || typeof mod.default?.setup !== "function") throw new Error("server plugin did not load")

const ALIASES = ["agent", "spread", "auto", "fast", "quality", "fair"]
const fail = (message) => { throw new Error(message) }

// Fake host: a provider registry whose editor.update really mutates the stored provider.
async function setup({ seed, integrationFails = false }) {
  const providers = new Map()
  const modelLists = new Map()
  if (seed) {
    providers.set("freellmpool", { provider: structuredClone(seed.provider), models: seed.models || [] })
  }
  const tools = new Map()
  const editor = {
    get: (id) => {
      const entry = providers.get(id)
      if (!entry) return undefined
      const list = modelLists.get(id) ?? entry.models
      return { provider: entry.provider, models: new Map(list.map((model) => [model.id, model])) }
    },
    add: ({ info, models }) => { providers.set(info.id, { provider: info, models }) },
    update: (id, fn) => { fn(providers.get(id).provider) },
    models: { set: (id, list) => { modelLists.set(id, list) } },
  }
  const ctx = {
    integration: {
      transform: async (fn) => {
        if (integrationFails) throw new Error("integration registry unavailable")
        fn({ get: () => undefined, update: () => {}, method: { update: () => {} } })
      },
    },
    provider: { transform: async (fn) => fn(editor) },
    tool: { transform: async (fn) => fn({ add: (tool) => tools.set(tool.name, tool) }) },
    event: { subscribe: () => (async function* () {})() },
  }
  const cleanup = await mod.default.setup(ctx)
  if (typeof cleanup !== "function") fail("server plugin setup did not return a cleanup function")
  cleanup()
  const entry = providers.get("freellmpool")
  return {
    tools,
    provider: entry?.provider,
    modelIDs: (modelLists.get("freellmpool") ?? entry?.models ?? []).map((model) => model.id),
  }
}

// 1. fresh host: provider, aliases, tools and the discovery opt-in are registered
const fresh = await setup({})
for (const name of ["freellmpool_status", "freellmpool_models", "freellmpool_tokenmax"]) {
  if (typeof fresh.tools.get(name)?.execute !== "function") fail("server plugin did not register " + name)
}
for (const alias of ALIASES) {
  if (!fresh.modelIDs.includes(alias)) fail("server plugin did not register provider model " + alias)
}
if (fresh.provider?.integrationID !== "freellmpool") fail("fresh provider was not linked to its integration")
if (!fresh.provider?.settings?.baseURL?.endsWith("/v1")) fail("fresh provider has no baseURL")
if (fresh.provider?.settings?.modelsDiscovery?.enabled !== true) fail("fresh provider ignored FREELLMPOOL_MODELS_DISCOVERY")

// 2. host already has a customised provider (no package, no integration): defaults are merged,
//    configured values and models survive
const existing = await setup({
  seed: {
    provider: {
      id: "freellmpool",
      name: "Custom pool",
      settings: { apiKey: "custom-api-key", extra: 1, modelsDiscovery: { enabled: true, endpoint: "/models" } },
    },
    models: [{ id: "custom" }],
  },
})
const p = existing.provider
if (p.name !== "Custom pool" || p.settings.apiKey !== "custom-api-key" || p.settings.extra !== 1) {
  fail("server plugin replaced existing provider configuration")
}
if (p.settings.modelsDiscovery?.endpoint !== "/models" || p.settings.modelsDiscovery?.enabled !== true) {
  fail("server plugin did not merge modelsDiscovery into the existing provider")
}
if (!p.package) fail("server plugin did not fill the missing provider package")
if (!p.settings.baseURL) fail("server plugin did not fill the missing baseURL")
if (p.integrationID !== "freellmpool") fail("server plugin did not attach the integration to the existing provider")
if (!existing.modelIDs.includes("custom")) fail("server plugin dropped an existing model")
for (const alias of ALIASES) {
  if (!existing.modelIDs.includes(alias)) fail("server plugin did not add alias " + alias + " to the existing provider")
}

// 3. integration registration fails: the provider must not reference a missing integration
const broken = await setup({ integrationFails: true })
if (broken.provider?.integrationID !== undefined) fail("provider was linked to an integration that failed to register")
if (!broken.modelIDs.includes("agent")) fail("provider models were lost when the integration failed")
`
  run(process.execPath, ["--input-type=module", "-e", script], { cwd: installDir })
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
