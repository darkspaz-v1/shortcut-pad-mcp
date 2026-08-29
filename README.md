# Shortcut Pad — MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that puts the
Shortcut Pad launcher behind natural language: *"open Focus Timer,"* *"what's on my
shortcut pad?"*, *"add Blender to the pad."*

It is a view onto the launcher's **existing** `config.json` — not a second copy of that
state — so anything you add here shows up in the Ctrl+Alt+L palette, and vice versa.

Built in Python with the official MCP SDK (`FastMCP`). **No external API, no API keys,
works offline.**

## Security model

`shortcutpad_launch` can **only** start entries that already exist in the launcher's
`config.json`. It never accepts a raw path or command from the caller — a request to
launch `C:\Windows\System32\calc.exe` is rejected unless "calc" is already an entry you
put on the pad. `config.json` is the allowlist, and you own it.

This matters because MCP tool calls can be influenced by content a model reads. An
allowlist means the worst case is "launched one of your own utilities", not "ran an
arbitrary executable".

`shortcutpad_add_item` does write to that allowlist, so:
- it refuses targets that don't exist on disk,
- it is annotated as a non-read-only tool, and
- adding and launching are two separate calls, so each surfaces to you for approval.

If you'd rather remove that path entirely, delete the `shortcutpad_add_item` and
`shortcutpad_remove_item` tools — the read + launch pair works fine on its own.

## Tools

| Tool | Purpose |
|------|---------|
| `shortcutpad_list_items` | List every shortcut on the pad |
| `shortcutpad_launch` | Launch one shortcut by name (allowlist-enforced) |
| `shortcutpad_add_item` | Add a new shortcut |
| `shortcutpad_remove_item` | Remove a shortcut (never deletes the target itself) |

Names match case-insensitively, and partial names work when unambiguous — `"focus tim"`
resolves to `Focus Timer`, while `"focus"` returns both candidates rather than guessing.

## Setup

Requires Python 3.10+ and the MCP SDK:

```bash
pip install "mcp[cli]"
```

## Register with Claude Code

```bash
claude mcp add shortcut-pad -- python "C:\Users\anshu\Desktop\Claude\shortcut-pad-mcp\server.py"
```

## Register with Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "shortcut-pad": {
      "command": "python",
      "args": ["C:\\Users\\anshu\\Desktop\\Claude\\shortcut-pad-mcp\\server.py"]
    }
  }
}
```

Restart Claude Desktop afterwards.

## Test

```bash
python test_server.py
```

Runs a temp-file smoke test covering all 4 tools, name resolution, ambiguity, the
allowlist guarantee, and stale-target handling. It never launches a program and never
touches your real `config.json`.

## Config

Reads `C:\Users\anshu\Desktop\Claude\launcher\config.json` by default. Override with the
`SHORTCUTPAD_CONFIG_FILE` environment variable.

The launcher app reads its config at startup, so restart it to see items added here.

## Tech

Python · MCP Python SDK (FastMCP) · Pydantic v2 · asyncio
