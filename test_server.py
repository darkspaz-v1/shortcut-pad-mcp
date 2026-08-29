#!/usr/bin/env python3
"""End-to-end smoke test: drives every tool through the real handlers.

Uses a temporary config file so it never touches your real launcher config.json,
and never actually starts a program — the launch tool is exercised only through
its resolution and validation paths.

Run:  python test_server.py
"""

import asyncio
import json
import os
import tempfile

# Point the server at a throwaway config BEFORE importing it.
_tmp = tempfile.mkdtemp()
_cfg = os.path.join(_tmp, "config.json")
with open(_cfg, "w", encoding="utf-8") as fh:
    json.dump({"hotkey": "ctrl+alt+l", "items": []}, fh)
os.environ["SHORTCUTPAD_CONFIG_FILE"] = _cfg

import server as s  # noqa: E402


def ok(cond: bool, label: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", label)
    assert cond, label


async def main() -> None:
    # A real file on disk to point shortcuts at.
    probe = os.path.join(_tmp, "probe.txt")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("x")

    # 1. Empty pad
    r = await s.shortcutpad_list_items(s.ListItemsInput())
    ok("no items yet" in r, "empty pad reports no items")

    # 2. Add items
    r = await s.shortcutpad_add_item(
        s.AddItemInput(name="Focus Timer", target=probe, hotkey="ctrl+alt+f")
    )
    ok("Added" in r and "ctrl+alt+f" in r, "add returns confirmation with hotkey")

    r = await s.shortcutpad_add_item(
        s.AddItemInput(name="Focus Music", target="https://example.com", type=s.ItemType.URL)
    )
    ok("Added" in r, "url item added without an on-disk target")

    # 3. Validation: duplicate name and missing target are both refused
    r = await s.shortcutpad_add_item(s.AddItemInput(name="focus timer", target=probe))
    ok("already exists" in r, "duplicate name rejected case-insensitively")

    r = await s.shortcutpad_add_item(
        s.AddItemInput(name="Ghost", target=os.path.join(_tmp, "nope.exe"))
    )
    ok("does not exist" in r, "nonexistent target rejected")

    # 4. List reflects both items
    data = json.loads(await s.shortcutpad_list_items(s.ListItemsInput(response_format=s.ResponseFormat.JSON)))
    ok(data["count"] == 2, "list returns both items")
    ok(data["items"][0]["hotkey"] == "ctrl+alt+f", "per-item hotkey persisted")

    # 5. Launch resolution — ambiguity and misses are caught before anything runs
    r = await s.shortcutpad_launch(s.LaunchInput(name="Focus"))
    ok("ambiguous" in r, "ambiguous prefix refuses to launch")

    r = await s.shortcutpad_launch(s.LaunchInput(name="Nonexistent App"))
    ok("No shortcut named" in r and "Focus Timer" in r, "unknown name lists valid names")

    # 6. Allowlist integrity: a raw path is not a name, so it cannot launch
    r = await s.shortcutpad_launch(s.LaunchInput(name=r"C:\Windows\System32\calc.exe"))
    ok("No shortcut named" in r, "raw path is rejected — allowlist enforced")

    # 7. A stale target is reported instead of raising
    os.remove(probe)
    r = await s.shortcutpad_launch(s.LaunchInput(name="Focus Timer"))
    ok("no longer exists" in r, "stale target gives an actionable error")

    # 8. Remove
    r = await s.shortcutpad_remove_item(s.RemoveItemInput(name="Focus Timer"))
    ok("Removed" in r, "remove returns confirmation")

    r = await s.shortcutpad_remove_item(s.RemoveItemInput(name="Focus Timer"))
    ok("No shortcut named" in r, "removing twice is caught")

    data = json.loads(await s.shortcutpad_list_items(s.ListItemsInput(response_format=s.ResponseFormat.JSON)))
    ok(data["count"] == 1, "one item left after removal")

    print("\nAll shortcut-pad tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
