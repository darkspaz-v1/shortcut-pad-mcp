#!/usr/bin/env python3
"""Rebuild docs/media/*.png for the README.

Starts the real server over stdio against a copy of examples/config.sample.json (invented items).
Nothing real is ever launched: the server is started through a tiny wrapper that replaces
os.startfile / webbrowser.open with no-ops and treats the sample's fake paths as existing, so the
launch handler runs its real logic up to the point where it would start a program. Raw responses go
to docs/media/demo-capture.txt and the flow is rendered into a terminal-style image.
Needs Pillow (`pip install pillow`) on top of requirements.txt. Run from the repo root:
python scripts/build_media.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(ROOT, "docs", "media")
SAMPLE = os.path.join(ROOT, "examples", "config.sample.json")

# Runs in the server's process: mock the two launch primitives, then run server.py as __main__.
WRAPPER = (
    "import os, runpy, sys, webbrowser\n"
    "_exists = os.path.exists\n"
    "os.path.exists = lambda p: True if str(p).replace(chr(92), '/').startswith(('C:/Tools/', 'C:/Users/Public/')) else _exists(p)\n"
    "os.startfile = lambda target: print('MOCK startfile:', target, file=sys.stderr)\n"
    "webbrowser.open = lambda url: print('MOCK webbrowser.open:', url, file=sys.stderr)\n"
    "runpy.run_path(sys.argv[1], run_name='__main__')\n"
)

# ---- rendering helpers (Pillow) --------------------------------------------

FONTS = "C:/Windows/Fonts/"
BG, PANEL, BAR = (13, 17, 23), (22, 27, 34), (33, 38, 45)
FG, DIM, ACCENT = (201, 209, 217), (125, 133, 144), (88, 166, 255)
GREEN, AMBER, PINK = (86, 211, 100), (227, 179, 65), (255, 123, 114)


def _font(name, size):
    try:
        return ImageFont.truetype(FONTS + name, size)
    except OSError:
        return ImageFont.load_default()


def terminal_png(path, caption, title, lines, width=1200, size=17, pad=28):
    """Render styled lines to a dark terminal-style PNG.

    lines: list of lines; each line is a list of (text, colour) segments, or None for a blank line.
    """
    mono = _font("consola.ttf", size)
    ui = _font("segoeui.ttf", 18)
    ui_b = _font("segoeuib.ttf", 20)
    lh = int(size * 1.5)
    cap_h, bar_h = 58, 40
    height = cap_h + bar_h + pad + lh * len(lines) + pad
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.text((pad, 16), caption, font=ui_b, fill=(240, 246, 252))
    top = cap_h
    d.rounded_rectangle((12, top, width - 12, height - 12), radius=10, fill=PANEL, outline=BAR)
    d.rounded_rectangle((12, top, width - 12, top + bar_h), radius=10, fill=BAR)
    d.rectangle((12, top + 20, width - 12, top + bar_h), fill=BAR)
    for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((30 + i * 24, top + 13, 44 + i * 24, top + 27), fill=col)
    d.text((120, top + 8), title, font=ui, fill=DIM)
    y = top + bar_h + pad // 2
    for segs in lines:
        x = pad + 8
        for text, col in segs or []:
            d.text((x, y), text, font=mono, fill=col)
            x += mono.getlength(text)
        y += lh
    img = img.quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    img.save(path, optimize=True)


def social_png(path, title, outcome, chips, footer):
    """1280x640 social preview: title, outcome sentence, tool chips, footer."""
    w, h = 1280, 640
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 14, h), fill=ACCENT)
    d.text((80, 90), title, font=_font("segoeuib.ttf", 84), fill=(240, 246, 252))
    y = 215
    for line in outcome:
        d.text((80, y), line, font=_font("segoeui.ttf", 38), fill=FG)
        y += 54
    mono = _font("consola.ttf", 26)
    x, y = 80, max(y + 40, 380)
    for chip in chips:
        tw = mono.getlength(chip)
        if x + tw + 40 > w - 60:
            x, y = 80, y + 64
        d.rounded_rectangle((x, y, x + tw + 32, y + 48), radius=10, fill=PANEL, outline=(48, 54, 61), width=2)
        d.text((x + 16, y + 8), chip, font=mono, fill=ACCENT)
        x += tw + 32 + 16
    d.text((80, h - 80), footer, font=_font("segoeui.ttf", 28), fill=DIM)
    img.quantize(colors=48, dither=Image.Dither.NONE).save(path, optimize=True)


def wrap_out(text, max_chars=104, max_lines=None):
    """Split captured tool output into display lines (no rewriting, only wrapping/truncation)."""
    out = []
    for raw in text.splitlines():
        while len(raw) > max_chars:
            cut = raw.rfind(" ", 0, max_chars)
            cut = cut if cut > 40 else max_chars
            out.append(raw[:cut])
            raw = "    " + raw[cut:].lstrip()
        out.append(raw)
    if max_lines and len(out) > max_lines:
        hidden = len(out) - max_lines
        out = out[:max_lines] + [f"[... {hidden} more line(s) not shown in this image]"]
    return out


async def capture(config_file):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", WRAPPER, os.path.join(ROOT, "server.py")],
        env={**os.environ, "SHORTCUTPAD_CONFIG_FILE": config_file},
    )
    calls = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)

            async def call(name, args):
                res = await session.call_tool(name, {"params": args})
                calls.append((name, args, res.content[0].text))

            await call("shortcutpad_list_items", {})
            await call("shortcutpad_launch", {"name": "focus timer"})
            await call("shortcutpad_launch", {"name": r"C:\Windows\System32\calc.exe"})
            await call("shortcutpad_launch", {"name": "Focus"})
    return tools, calls


def main():
    os.makedirs(MEDIA, exist_ok=True)
    work = tempfile.mkdtemp()
    config_file = os.path.join(work, "config.json")
    shutil.copy(SAMPLE, config_file)  # the tracked sample is never modified
    tools, calls = asyncio.run(capture(config_file))
    shutil.rmtree(work, ignore_errors=True)

    with open(os.path.join(MEDIA, "demo-capture.txt"), "w", encoding="utf-8") as fh:
        fh.write("# Raw responses from server.py against a copy of examples/config.sample.json\n")
        fh.write("# (os.startfile and webbrowser.open are mocked; nothing was launched)\n")
        fh.write(f"# tools/list -> {', '.join(tools)}\n")
        for name, args, out in calls:
            fh.write(f"\n## {name} {json.dumps(args)}\n{out}\n")

    def show(idx, note=None):
        name, args, out = calls[idx]
        lines = [[("agent> ", GREEN), (f"{name} {json.dumps(args)}", AMBER)]]
        for ln in wrap_out(out, max_chars=108):
            lines.append([("  " + ln, FG)])
        if note:
            lines.append([("  " + note, DIM)])
        return lines

    L = [[("You: ", ACCENT), ("What can you open for me? Start the focus timer. Also run calc.exe from System32.", FG)], None]
    L += show(0) + [None]
    L += show(1, "(mocked: the launch call was replaced by a no-op, nothing started)") + [None]
    L += show(2, "(refused: a raw path is not a name on the allowlist, so it never reaches the launcher)")
    L.append(None)
    L.append([("(agent reply omitted; every tool call and result above is real server output)", DIM)])
    terminal_png(
        os.path.join(MEDIA, "demo.png"),
        "Example session against a fake launcher config (launch mocked, nothing was started)",
        "shortcut-pad-mcp  |  list -> launch by name -> raw path refused",
        L,
    )
    social_png(
        os.path.join(MEDIA, "social-preview.png"),
        "shortcut-pad-mcp",
        ["Let an AI assistant launch your apps by name.", "Only names already on your allowlist."],
        ["shortcutpad_list_items", "shortcutpad_launch", "shortcutpad_add_item", "shortcutpad_remove_item"],
        "MCP server  |  Python  |  Windows  |  never accepts a path or command",
    )
    for f in ("demo.png", "social-preview.png"):
        print(f, os.path.getsize(os.path.join(MEDIA, f)) // 1024, "KB")


if __name__ == "__main__":
    main()
