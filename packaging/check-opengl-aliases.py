#!/usr/bin/env python3
"""Validate the Wine alias patch against extracted source and Khronos gl.xml.

No downloads or source mutations. By default the tracked patch is applied in
memory. Use --applied if the Wine source already contains the patch. --self-test
also verifies that missing registry aliases and changed signatures are rejected.
"""

import argparse
import copy
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET


EXTENSION = "GL_EXT_gpu_shader4"
PAIR_COUNT = 34
PATCH = Path(__file__).parent / "patches/wine-opengl-promoted-aliases.patch"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def apply_patch(source, patch):
    """Apply one-file unified diff in memory, requiring exact original lines."""
    lines = source.splitlines(keepends=True)
    diff = patch.splitlines(keepends=True)
    require(diff[:2] == ["--- a/dlls/opengl32/unix_wgl.c\n",
                         "+++ b/dlls/opengl32/unix_wgl.c\n"],
            "Patch must modify only dlls/opengl32/unix_wgl.c")
    output = []
    cursor = 0
    index = 2
    while index < len(diff):
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff[index])
        require(match is not None, f"Invalid patch hunk: {diff[index].rstrip()}")
        start = int(match[1]) - 1
        require(start >= cursor, "Patch hunks overlap")
        output.extend(lines[cursor:start])
        cursor = start
        old_count = new_count = 0
        index += 1
        while index < len(diff) and not diff[index].startswith("@@ "):
            line = diff[index]
            require(line[:1] in (" ", "+", "-"), "Unsupported patch line")
            if line[0] in (" ", "-"):
                require(cursor < len(lines) and lines[cursor] == line[1:],
                        f"Patch context mismatch at source line {cursor + 1}; "
                        "use --applied only for an already patched source")
                cursor += 1
                old_count += 1
            if line[0] in (" ", "+"):
                output.append(line[1:])
                new_count += 1
            index += 1
        require(old_count == int(match[2] or 1) and new_count == int(match[4] or 1),
                "Patch hunk line counts do not match")
    output.extend(lines[cursor:])
    return "".join(output)


def without_comments(source):
    # Preserve strings: extension strings and names are checked below.
    return re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                  lambda match: match[0] if match[0].startswith('"') else " ",
                  source, flags=re.S)


def block(source, opening):
    depth = 0
    quoted = escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise ValueError("Unclosed C block")


def declaration_type(element):
    text = element.text or ""
    for child in element:
        if child.tag != "name":
            text += "".join(child.itertext())
        text += child.tail or ""
    return tuple(re.findall(r"\w+|\*|\[|\]", text))


def xml_signature(command):
    return (declaration_type(command.find("proto")),
            tuple(declaration_type(param) for param in command.findall("param")))


def wine_signature(source, name):
    match = re.search(r"static\s+([^\n]+?)\s+WINAPI\s+" + re.escape(name) +
                      r"\s*\(([^)]*)\)", source)
    require(match is not None, f"Wine PE thunk is missing: {name}")
    params = []
    for param in match[2].split(","):
        tokens = re.findall(r"\w+|\*|\[|\]", param)
        require(len(tokens) >= 2, f"Cannot normalize Wine signature for {name}")
        # These commands use named scalar/pointer arguments, without arrays or callbacks.
        require("[" not in tokens and "]" not in tokens,
                f"Unexpected array signature for {name}")
        params.append(tuple(tokens[:-1]))
    return (tuple(re.findall(r"\w+|\*", match[1])), tuple(params))


def validate(wine_root, registry, wrapper):
    xml = ET.parse(registry).getroot()
    commands = {command.findtext("proto/name"): command
                for command in xml.findall("commands/command")
                if command.find("proto") is not None}
    feature = xml.find("feature[@name='GL_VERSION_3_0']")
    extension = xml.find(f"extensions/extension[@name='{EXTENSION}']")
    require(feature is not None and extension is not None,
            "Registry is missing desktop GL 3.0 or EXT_gpu_shader4")
    require(feature.get("api") == "gl" and "gl" in extension.get("supported", "").split("|"),
            "Registry entries must describe desktop OpenGL")
    core_commands = {command.get("name") for command in feature.findall("require/command")}
    ext_commands = {command.get("name") for command in extension.findall("require/command")}
    pairs = set()
    for name in ext_commands:
        command = commands.get(name)
        require(command is not None, f"Registry is missing command {name}")
        alias = command.find("alias")
        require(alias is not None and alias.get("name") in core_commands,
                f"Missing GL 3.0 registry alias for {name}")
        pairs.add((alias.get("name"), name))
    require(len(pairs) == PAIR_COUNT, f"Expected {PAIR_COUNT} aliases, got {len(pairs)}")

    wrapper = without_comments(wrapper)
    entry = re.search(r"PROC\s+wrap_wglGetProcAddress\s*\([^)]*\)\s*\{", wrapper)
    require(entry is not None, "Wine is missing wrap_wglGetProcAddress")
    function = block(wrapper, entry.end() - 1)
    table = re.search(r"alternatives\[\]\s*=\s*\{", function)
    require(table is not None, "Wine is missing the explicit alternatives table")
    actual_pairs = re.findall(r'\{\s*"(gl\w+)"\s*,\s*"(gl\w+)"\s*\}',
                              block(function, table.end() - 1))
    added_pairs = [(core, ext) for core, ext in actual_pairs if ext in ext_commands]
    require(len(added_pairs) == PAIR_COUNT and set(added_pairs) == pairs,
            "Wine alternatives do not contain exactly the 34 registry aliases")
    existing_pairs = {("glCopyTexSubImage3DEXT", "glCopyTexSubImage3D"),
                      ("glVertexAttribDivisor", "glVertexAttribDivisorARB"),
                      ("glCompressedTexImage2DARB", "glCompressedTexImage2D")}
    require(len(actual_pairs) == PAIR_COUNT + len(existing_pairs) and
            set(actual_pairs) == pairs | existing_pairs,
            "Wine alternatives contain an unexpected or duplicate fallback")

    # This source guard must precede the shared driver-pointer cache. Otherwise a
    # lookup made on another context can bypass the current extension/version gate.
    current = re.search(r"get_current_context\s*\(\s*teb\s*,\s*NULL\s*,\s*NULL\s*\)", function)
    gate = re.search(r"if\s*\(\s*!is_any_extension_supported\s*\(\s*ctx\s*,\s*found->extension\s*\)\s*\)\s*\{",
                     function)
    cache = re.search(r"if\s*\(\s*!\*func_ptr\s*\)", function)
    require(current and gate and cache and current.start() < gate.start() < cache.start(),
            "Current-context capability check must precede the driver-pointer cache")
    guarded = block(function, gate.end() - 1)
    require(re.search(r"if\s*\(\s*strcmp\s*\(\s*name\s*,\s*alternatives\[i\]\.name\s*\)\s*\)\s*continue\s*;", guarded),
            "Alias fallback must match an explicit name")
    require(re.search(r"return\s+wrap_wglGetProcAddress\s*\(\s*teb\s*,\s*alternatives\[i\]\.alt\s*\)\s*;", guarded),
            "Alias fallback must recurse through the extension capability check")
    require(re.search(r"return\s*\(void\s*\*\)\s*-1\s*;", guarded),
            "Unsupported names must still be rejected")
    require(not re.search(r"ctx->(?:major|minor)_version\s*=", function),
            "Function resolution must not alter the context version")

    thunk_source = (wine_root / "dlls/opengl32/thunks.c").read_text()
    registry_source = (wine_root / "dlls/opengl32/unix_thunks.c").read_text()
    entries = {match[1]: match[2].split(r"\0")
               for match in re.finditer(r'\{\s*"(gl\w+)"\s*,\s*"([^"\n]*)"\s*,\s*offsetof\(struct opengl_funcs,\s*p_\1\)\s*\}',
                                         registry_source)}
    for core, ext in sorted(pairs):
        require(core in commands, f"Registry is missing canonical command {core}")
        canonical = xml_signature(commands[core])
        require(canonical == xml_signature(commands[ext]),
                f"Registry signature mismatch: {core} / {ext}")
        require(canonical == wine_signature(thunk_source, core) == wine_signature(thunk_source, ext),
                f"Wine thunk signature mismatch: {core} / {ext}")
        require("GL_VERSION_3_0" in entries.get(core, []),
                f"Wine core requirement is missing: {core}")
        require(EXTENSION in entries.get(ext, []),
                f"Wine extension requirement is missing: {ext}")
    return pairs


def self_test(wine_root, registry, wrapper):
    for label, mutation in (("missing alias", "alias"), ("changed signature", "signature")):
        tree = copy.deepcopy(ET.parse(registry))
        command = next(item for item in tree.findall("commands/command")
                       if item.findtext("proto/name") == "glVertexAttribIPointerEXT")
        if mutation == "alias":
            command.remove(command.find("alias"))
        else:
            command.find("param/ptype").text = "GLfloat"
        with tempfile.TemporaryDirectory(prefix="gluon-alias-check-") as directory:
            changed = Path(directory) / "gl.xml"
            tree.write(changed, encoding="utf-8", xml_declaration=True)
            try:
                validate(wine_root, changed, wrapper)
            except ValueError as error:
                require(("alias" if mutation == "alias" else "signature mismatch") in str(error),
                        f"Unexpected rejection for {label}: {error}")
                print(f"Rejected negative case ({label}): {error}")
            else:
                raise ValueError(f"Negative case was incorrectly accepted: {label}")

    # Model the original cache hazard with valid C: put the extension guard inside
    # the pointer-cache condition, where later contexts would skip it.
    source = without_comments(wrapper)
    gate = re.search(r"if\s*\(\s*!is_any_extension_supported\s*\(\s*ctx\s*,\s*found->extension\s*\)\s*\)\s*\{", source)
    guarded = block(source, gate.end() - 1)
    gate_end = gate.end() + len(guarded) + 1
    moved = source[:gate.start()] + source[gate_end:]
    cache = re.search(r"if\s*\(\s*!\*func_ptr\s*\)\s*\{", moved)
    moved = moved[:cache.end()] + source[gate.start():gate_end] + moved[cache.end():]
    try:
        validate(wine_root, registry, moved)
    except ValueError as error:
        require("capability check must precede" in str(error),
                f"Unexpected rejection for cached pointer bypass: {error}")
        print(f"Rejected negative case (cached pointer bypass): {error}")
    else:
        raise ValueError("Negative case was incorrectly accepted: cached pointer bypass")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--applied", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        wrapper = (args.wine_source / "dlls/opengl32/unix_wgl.c").read_text()
        if not args.applied:
            wrapper = apply_patch(wrapper, PATCH.read_text())
        pairs = validate(args.wine_source, args.registry, wrapper)
        print(f"Validated {len(pairs)} desktop GL 3.0 aliases, exact signatures, "
              "extension entries and current-context fallback guards")
        if args.self_test:
            self_test(args.wine_source, args.registry, wrapper)
    except (ValueError, OSError, ET.ParseError) as error:
        print(f"OpenGL alias validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
