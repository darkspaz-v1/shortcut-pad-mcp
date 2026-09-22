#!/usr/bin/env python3
"""
Shortcut Pad MCP Server.

A local, self-contained MCP server that exposes the Shortcut Pad launcher
(its config.json; set SHORTCUTPAD_CONFIG_FILE) to any MCP client, so an AI assistant
can list and launch your Windows utility apps by name instead of you opening the
palette with Ctrl+Alt+L.

SECURITY MODEL — read this before extending:
    `shortcutpad_launch` can ONLY start entries that already exist in the
    launcher's config.json. It never accepts a raw path or command from the
    caller. config.json is the allowlist and you control it. This keeps a
    compromised or prompt-injected model from executing arbitrary programs.

    `shortcutpad_add_item` DOES write new entries to that allowlist, which is
    why it is annotated as non-read-only and validates that the target exists
    on disk first. Adding then launching is a deliberate two-call sequence so
    each step surfaces to you separately for approval.

Data lives in the launcher's own config.json — this server is a view onto the
existing app, not a second copy of its state. Edits made here show up in the
Ctrl+Alt+L palette the next time it reloads.

Tools:
    - shortcutpad_list_items    List every shortcut on the pad.
    - shortcutpad_launch        Launch one shortcut by name (allowlist-enforced).
    - shortcutpad_add_item      Add a new shortcut to the pad.
    - shortcutpad_remove_item   Remove a shortcut from the pad.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration & storage
# ---------------------------------------------------------------------------

mcp = FastMCP("shortcutpad_mcp")
log = logging.getLogger("shortcutpad_mcp")  # stdio transport: logs go to stderr, never stdout

# The launcher app's own config. Set SHORTCUTPAD_CONFIG_FILE; the fallback is
# <home>/Desktop/Claude/launcher/config.json (home = %USERPROFILE% on Windows).
CONFIG_FILE = os.environ.get(
    "SHORTCUTPAD_CONFIG_FILE",
    os.path.join(
        os.path.expanduser("~"), "Desktop", "Claude", "launcher", "config.json"
    ),
)

# A single lock guards all reads/writes so concurrent tool calls stay consistent.
_LOCK = threading.Lock()


class ItemType(str, Enum):
    """How a shortcut target should be opened."""

    APP = "app"
    URL = "url"
    FOLDER = "folder"


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


# ---------------------------------------------------------------------------
# Shared storage helpers
# ---------------------------------------------------------------------------


def _load() -> Dict[str, Any]:
    """Read the launcher config from disk. Returns a valid empty shape on failure."""
    if not os.path.exists(CONFIG_FILE):
        return {"hotkey": "ctrl+alt+l", "items": []}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"hotkey": "ctrl+alt+l", "items": []}
        data.setdefault("items", [])
        if not isinstance(data["items"], list):
            data["items"] = []
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read launcher config %s: %s", CONFIG_FILE, exc)
        return {"hotkey": "ctrl+alt+l", "items": []}


def _save(cfg: Dict[str, Any]) -> None:
    """Persist the launcher config atomically, preserving the app's formatting."""
    tmp = f"{CONFIG_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, CONFIG_FILE)


def _match_item(
    items: List[Dict[str, Any]], query: str
) -> Tuple[Optional[Dict[str, Any]], List[str], bool]:
    """Resolve a user-supplied name to exactly one shortcut.

    Matching is progressively looser: exact (case-insensitive), then prefix,
    then substring.

    Returns (item, candidates, ambiguous):
        - item is the single resolved shortcut, or None.
        - candidates are the names to show the caller — the competing matches
          when ambiguous, otherwise every name on the pad.
        - ambiguous distinguishes "several matched" from "none matched", so
          callers never have to infer it from list lengths.
    """
    q = query.lower().strip()
    names = [i.get("name", "") for i in items]

    exact = [i for i in items if i.get("name", "").lower() == q]
    if len(exact) == 1:
        return exact[0], names, False

    for tier in (
        [i for i in items if i.get("name", "").lower().startswith(q)],
        [i for i in items if q in i.get("name", "").lower()],
    ):
        if len(tier) == 1:
            return tier[0], names, False
        if len(tier) > 1:
            return None, [i.get("name", "") for i in tier], True

    return None, names, False


def _format_item_markdown(item: Dict[str, Any]) -> str:
    """Render one shortcut as a markdown bullet."""
    line = f"- **{item.get('name', '(unnamed)')}** — `{item.get('type', 'app')}`"
    if item.get("hotkey"):
        line += f" · hotkey `{item['hotkey']}`"
    line += f"\n    - Target: `{item.get('target', '')}`"
    return line


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class ListItemsInput(BaseModel):
    """Input for listing the shortcuts on the pad."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class LaunchInput(BaseModel):
    """Input for launching an existing shortcut."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ...,
        description=(
            "Name of the shortcut to launch, e.g. 'Focus Timer'. Matched "
            "case-insensitively against names already on the pad; partial names "
            "work when unambiguous. Raw file paths are NOT accepted."
        ),
        min_length=1,
        max_length=200,
    )

    @field_validator("name")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty or whitespace only")
        return v.strip()


class AddItemInput(BaseModel):
    """Input for adding a new shortcut to the pad."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ..., description="Display name for the shortcut (e.g. 'Focus Timer')",
        min_length=1, max_length=200,
    )
    target: str = Field(
        ...,
        description=(
            "What to open: an absolute path to a .exe/.bat/.lnk/.vbs, a folder "
            "path, or a URL when type is 'url'."
        ),
        min_length=1,
        max_length=500,
    )
    type: ItemType = Field(
        default=ItemType.APP,
        description="How to open the target: 'app', 'url', or 'folder'.",
    )
    hotkey: Optional[str] = Field(
        default=None,
        description="Optional dedicated hotkey for this item (e.g. 'ctrl+alt+j').",
        max_length=50,
    )


class RemoveItemInput(BaseModel):
    """Input for removing a shortcut from the pad."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ..., description="Name of the shortcut to remove (same matching rules as launch)",
        min_length=1, max_length=200,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="shortcutpad_list_items",
    annotations={
        "title": "List Shortcut Pad Items",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def shortcutpad_list_items(params: ListItemsInput) -> str:
    """List every shortcut currently on the Shortcut Pad.

    Call this before shortcutpad_launch to discover valid names.

    Args:
        params (ListItemsInput): Validated input containing:
            - response_format (ResponseFormat): 'markdown' or 'json'.

    Returns:
        str: In json, an object of the form:
            {
              "hotkey": str,        # the pad's global open hotkey
              "count": int,
              "items": [ {"name", "type", "target", "hotkey"?}, ... ]
            }
            In markdown, the same shortcuts formatted for reading.
    """
    with _LOCK:
        cfg = _load()

    items = cfg.get("items", [])

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(
            {"hotkey": cfg.get("hotkey"), "count": len(items), "items": items},
            indent=2,
            ensure_ascii=False,
        )

    if not items:
        return (
            f"The Shortcut Pad has no items yet. Config file: {CONFIG_FILE}\n"
            f"Use shortcutpad_add_item to add one."
        )

    lines = [
        f"# Shortcut Pad ({len(items)} items)",
        f"_Open the palette with `{cfg.get('hotkey', 'ctrl+alt+l')}`._",
        "",
    ]
    for item in items:
        lines.append(_format_item_markdown(item))
    return "\n".join(lines)


@mcp.tool(
    name="shortcutpad_launch",
    annotations={
        "title": "Launch Shortcut",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def shortcutpad_launch(params: LaunchInput) -> str:
    """Launch a shortcut that is already on the Shortcut Pad.

    Only names present in the launcher's config.json can be launched — this
    tool cannot run an arbitrary path or command. If the name does not resolve
    to exactly one shortcut, nothing is launched and the valid names are
    returned instead.

    Args:
        params (LaunchInput): Validated input containing:
            - name (str): Name of an existing shortcut.

    Returns:
        str: Confirmation of what was launched, or an actionable error listing
             the available names.
    """
    with _LOCK:
        cfg = _load()
        items = cfg.get("items", [])

    if not items:
        return (
            f"Error: The Shortcut Pad is empty, so there is nothing to launch. "
            f"Config file: {CONFIG_FILE}"
        )

    item, candidates, ambiguous = _match_item(items, params.name)
    if item is None:
        if ambiguous:
            return (
                f"Error: '{params.name}' is ambiguous — it matches "
                f"{', '.join(repr(c) for c in candidates)}. "
                f"Call shortcutpad_launch again with the full name."
            )
        return (
            f"Error: No shortcut named '{params.name}'. Available: "
            f"{', '.join(repr(c) for c in candidates)}. "
            f"Use shortcutpad_list_items for details."
        )

    target = item.get("target", "")
    item_type = item.get("type", "app")

    try:
        if item_type == "url":
            import webbrowser

            webbrowser.open(target)
        else:
            if not os.path.exists(target):
                return (
                    f"Error: '{item['name']}' points at `{target}`, which no longer "
                    f"exists on disk. Fix the path in {CONFIG_FILE}, or remove the "
                    f"entry with shortcutpad_remove_item."
                )
            os.startfile(target)  # noqa: S606 — allowlisted target only
    except OSError as exc:
        return f"Error launching '{item['name']}': {exc}"

    return f"Launched **{item['name']}** ({item_type}) — `{target}`."


@mcp.tool(
    name="shortcutpad_add_item",
    annotations={
        "title": "Add Shortcut to Pad",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def shortcutpad_add_item(params: AddItemInput) -> str:
    """Add a new shortcut to the Shortcut Pad.

    This extends the launcher's allowlist, so the new entry becomes launchable
    by shortcutpad_launch and appears in the Ctrl+Alt+L palette. App and folder
    targets must already exist on disk; URLs are accepted as-is.

    Args:
        params (AddItemInput): Validated input containing:
            - name (str): Display name.
            - target (str): Path or URL to open.
            - type (ItemType): 'app', 'url', or 'folder'.
            - hotkey (Optional[str]): Dedicated hotkey for this item.

    Returns:
        str: Confirmation, or an actionable error if the name is taken or the
             target does not exist.
    """
    with _LOCK:
        cfg = _load()
        items = cfg.get("items", [])

        if any(i.get("name", "").lower() == params.name.lower() for i in items):
            return (
                f"Error: A shortcut named '{params.name}' already exists. "
                f"Remove it first with shortcutpad_remove_item, or pick another name."
            )

        if params.type != ItemType.URL and not os.path.exists(params.target):
            return (
                f"Error: Target `{params.target}` does not exist on disk. "
                f"Pass an absolute path to an existing file or folder, or set "
                f"type='url' if this is a web link."
            )

        item: Dict[str, Any] = {
            "name": params.name,
            "type": params.type.value,
            "target": params.target,
        }
        if params.hotkey:
            item["hotkey"] = params.hotkey
        items.append(item)
        cfg["items"] = items
        _save(cfg)

    hk = f" with hotkey `{params.hotkey}`" if params.hotkey else ""
    return (
        f"Added **{params.name}** ({params.type.value}){hk} to the Shortcut Pad. "
        f"Restart the launcher for the palette to pick it up."
    )


@mcp.tool(
    name="shortcutpad_remove_item",
    annotations={
        "title": "Remove Shortcut from Pad",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def shortcutpad_remove_item(params: RemoveItemInput) -> str:
    """Remove a shortcut from the Shortcut Pad.

    Only the launcher entry is removed — the target program or folder itself is
    never deleted.

    Args:
        params (RemoveItemInput): Validated input containing:
            - name (str): Name of the shortcut to remove.

    Returns:
        str: Confirmation, or an actionable error if the name does not resolve.
    """
    with _LOCK:
        cfg = _load()
        items = cfg.get("items", [])
        item, candidates, ambiguous = _match_item(items, params.name)
        if item is None:
            if ambiguous:
                return (
                    f"Error: '{params.name}' is ambiguous — it matches "
                    f"{', '.join(repr(c) for c in candidates)}. Nothing was removed."
                )
            return (
                f"Error: No shortcut named '{params.name}'. Available: "
                f"{', '.join(repr(c) for c in candidates)}. Nothing was removed."
            )
        cfg["items"] = [i for i in items if i is not item]
        _save(cfg)

    return (
        f"Removed **{item['name']}** from the Shortcut Pad. "
        f"The target itself (`{item.get('target', '')}`) was not deleted."
    )


if __name__ == "__main__":
    mcp.run()
