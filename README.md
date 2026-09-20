# shortcut-pad-mcp

MCP server over a Windows launcher config — list and launch local apps by name, allowlist-enforced.

## Tools

| Tool | Does |
|---|---|
| `shortcutpad_list_items` | Every configured entry |
| `shortcutpad_launch` | Start one **by name** |
| `shortcutpad_add_item` | Add an entry |
| `shortcutpad_remove_item` | Remove one |

## The design decision worth stating

**The launch tool takes a name, never a path or a command line.** It resolves that name against the
existing `config.json` and refuses anything not already there. An MCP tool that accepted an arbitrary
command would be a remote shell wearing a launcher costume — the allowlist is the entire security
model, so it is enforced at the only entry point rather than validated afterwards.

## Notes

- Tests use a **temporary config** and never start a real program: the launch path is exercised
  through its resolution and validation logic only.
- Removing a non-existent item is an error, not a silent no-op, so a typo surfaces immediately.

**14 checks pass.**

## About MCP

[Model Context Protocol](https://modelcontextprotocol.io) is a standard for exposing tools to an LLM
client. This server speaks MCP over stdio, so it is registered in the client config rather than run
directly.

```json
{
  "mcpServers": {
    "shortcut-pad": { "command": "python", "args": ["C:/path/to/shortcut-pad-mcp/server.py"] }
  }
}
```

**Register it twice if you use both Claude Code and Claude Desktop.** They read separate config files,
and a server registered in one is invisible to the other — this cost real debugging time.

## Tests

```
python test_server.py
```

Drives every tool through the real handlers and prints one `PASS` line per check. No pytest — the
suite is a single script so it runs anywhere with no dev dependencies.

## License

MIT — see [LICENSE](LICENSE).
