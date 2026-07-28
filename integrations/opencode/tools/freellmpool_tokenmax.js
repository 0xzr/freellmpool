import plugin from "opencode-freellmpool"

const loaded = await plugin({ client: { tui: { showToast: async () => {} } } })

export default loaded.tool.freellmpool_tokenmax
