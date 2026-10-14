#!/usr/bin/env python3
"""Check native Wine portability declarations against a pinned Khronos registry.

This reads source text only. It does not compile or load Wine or graphics code.
Without --applied, apply the supplied patch in memory before checking the source.
"""
import argparse
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET

REGISTRY_SHA256 = "7cf3d7ec74371f4395d221c05aa23d994a5e03d0ccfb6269c60399d333551bcd"
SOURCE_RELATIVE = "dlls/win32u/vulkan.c"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def apply_patch_in_memory(source, patch):
    """Accept only exact hunks against the one native file this patch owns."""
    rows = patch.splitlines(keepends=True)
    require(rows[:2] == [f"--- a/{SOURCE_RELATIVE}\n", f"+++ b/{SOURCE_RELATIVE}\n"],
            "Patch must change only native win32u/vulkan.c")
    old = source.splitlines(keepends=True)
    output, cursor, index = [], 0, 2
    while index < len(rows):
        match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*\n", rows[index])
        require(match is not None, "Unexpected patch hunk header")
        start = int(match[1]) - 1
        require(start >= cursor, "Patch hunks overlap")
        output.extend(old[cursor:start])
        cursor = start
        before = len(output)
        consumed = 0
        index += 1
        while index < len(rows) and not rows[index].startswith("@@ "):
            row = rows[index]
            require(row and row[0] in " +-", "Unexpected patch hunk content")
            if row[0] in " -":
                require(cursor < len(old) and old[cursor] == row[1:], "Wine source differs from patch context")
                cursor += 1
                consumed += 1
            if row[0] in " +":
                output.append(row[1:])
            index += 1
        require(consumed == int(match[2] or 1), "Wrong old hunk line count")
        require(len(output) - before == int(match[4] or 1), "Wrong new hunk line count")
    output.extend(old[cursor:])
    return "".join(output)


def registry_schema(data):
    require(hashlib.sha256(data).hexdigest() == REGISTRY_SHA256, "Unexpected Vulkan registry digest")
    registry = ET.fromstring(data)
    layout = registry.find('./types/type[@name="VkPhysicalDevicePortabilitySubsetFeaturesKHR"]')
    require(layout is not None, "Missing normative portability feature structure")
    members = []
    for member in layout.findall("member"):
        type_name, name = member.findtext("type"), member.findtext("name")
        # Pointer punctuation lives outside the XML <type> element.
        pointer = "*" in "".join(member.itertext())
        members.append((type_name + ("*" if pointer else ""), name))
    require(members[:2] == [("VkStructureType", "sType"), ("void*", "pNext")], "Unexpected native structure header")
    require(len(members) == 17 and all(t == "VkBool32" for t, _ in members[2:]), "Unexpected portability feature ABI")
    extension = registry.find('./extensions/extension[@name="VK_KHR_portability_subset"]')
    require(extension is not None and extension.get("type") == "device", "Missing normative device extension")
    enum = extension.find('./require/enum[@name="VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PORTABILITY_SUBSET_FEATURES_KHR"]')
    require(enum is not None and enum.get("extends") == "VkStructureType", "Missing normative structure type")
    enum_value = 1_000_000_000 + (int(extension.get("number")) - 1) * 1000 + int(enum.get("offset"))
    return members, enum_value


def validate_source(source, members, enum_value):
    declaration = re.search(r"struct native_portability_subset_features\s*\{([^}]+)\};", source)
    require(declaration is not None, "Missing native portability declaration")
    actual = []
    for row in declaration[1].strip().splitlines():
        field = re.fullmatch(r"\s*(VkStructureType|VkBool32|void)\s*(\*)?\s*(\w+)\s*;\s*", row)
        require(field is not None, "Unexpected native portability member declaration")
        actual.append((field[1] + (field[2] or ""), field[3]))
    require(actual == members, "Native portability field types or order differ from vk.xml")
    stype = re.search(r"#define NATIVE_PORTABILITY_SUBSET_FEATURES_STYPE \(\(VkStructureType\)(\d+)\)", source)
    require(stype is not None and int(stype[1]) == enum_value, "Native structure type differs from vk.xml")
    require('native_portability_subset_extension[] = "VK_KHR_portability_subset";' in source,
            "Wrong native extension name")
    start = source.find("static VkResult get_native_portability_subset_features(")
    end = source.find("static VkResult convert_device_create_info(", start)
    require(start >= 0 and end > start, "Missing native feature-query helper")
    helper = source[start:end]
    for snippet in (
        "*portability = NULL;", "attempt < 3", "count > SIZE_MAX / sizeof(*properties)",
        "physical_device->host.physical_device,", "if (res != VK_INCOMPLETE) break;",
        "if (res == VK_INCOMPLETE)", "res = VK_ERROR_INITIALIZATION_FAILED;",
        "if (!count) goto done;", "if (res) goto done;", "if (i == count) goto done;",
        "instance->p_vkGetPhysicalDeviceFeatures2;", "instance->p_vkGetPhysicalDeviceFeatures2KHR;",
        "if (!get_features)", "mem_alloc( pool, sizeof(**portability) )",
        "memset( *portability, 0, sizeof(**portability) );", "features.pNext = *portability;",
        "get_features( physical_device->host.physical_device, &features );", "free( properties );", "return res;",
    ):
        require(snippet in helper, "Missing native query/error guard: " + snippet)
    require("VK_TRUE" not in helper, "Native portability helper must not force supported bits")
    for _, field in members[2:]:
        require(re.search(r"->" + re.escape(field) + r"\s*=", helper) is None,
                "Native portability feature must come only from the host query: " + field)
    require(helper.index("if (i == count) goto done;") < helper.index("features.pNext = *portability;"),
            "Native features query must follow positive host advertisement")
    convert = source[end:source.find("static void init_device_queues(", end)]
    for snippet in (
        "if ((res = get_native_portability_subset_features( physical_device, pool, &portability ))) return res;",
        "if (portability)", "portability->pNext = (void *)info->pNext;", "info->pNext = portability;",
        "(sizeof(device->extensions) * 8 + 1) * sizeof(*extensions)",
        "if (i == count) extensions[count++] = native_portability_subset_extension;",
    ):
        require(snippet in convert, "Missing native create-info guard: " + snippet)
    require("client_physical_device->extensions" not in convert,
            "Native requirement must not change client extension advertisement")
    prerequisite = ("if (vulkan_funcs.host_extensions.has_VK_KHR_get_physical_device_properties2)\n"
                    "        instance->obj.extensions.has_VK_KHR_get_physical_device_properties2 = 1;")
    require(prerequisite in source, "Vulkan 1.0 native query prerequisite must be conditional on host support")
    require("VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR" not in source,
            "Direct MoltenVK binding must not add an SDK-loader portability enumeration flag")


def self_test(source, members, enum_value):
    first, second = members[2][1], members[3][1]
    swapped = source.replace("VkBool32 " + first + ";", "VkBool32 swapped;").replace(
        "VkBool32 " + second + ";", "VkBool32 " + first + ";").replace("VkBool32 swapped;", "VkBool32 " + second + ";")
    mutations = [
        swapped,
        source.replace(f"((VkStructureType){enum_value})", f"((VkStructureType){enum_value + 1})"),
        source.replace("get_features( physical_device->host.physical_device, &features );", "/* removed query */"),
        source.replace("if (vulkan_funcs.host_extensions.has_VK_KHR_get_physical_device_properties2)", "if (1)"),
        source.replace("features.pNext = *portability;", "features.pNext = *portability;\n    (*portability)->events = VK_TRUE;"),
    ]
    for index, mutated in enumerate(mutations):
        try:
            validate_source(mutated, members, enum_value)
        except ValueError:
            continue
        raise ValueError(f"Source validator missed mutation {index + 1}")
    print(f"Source-validator negative fixtures passed: {len(mutations)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--applied", action="store_true")
    parser.add_argument("--patch", type=Path, default=Path(__file__).parent / "patches/wine-vulkan-portability.patch")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    members, enum_value = registry_schema(args.registry.read_bytes())
    source = (args.wine_source / SOURCE_RELATIVE).read_text()
    header = (args.wine_source / "include/wine/vulkan.h").read_text()
    require("#define VK_HEADER_VERSION 335" in header, "Expected the pinned Wine Vulkan header version")
    require("PORTABILITY_SUBSET" not in header, "Generated Windows API must continue to filter portability_subset")
    if not args.applied:
        source = apply_patch_in_memory(source, args.patch.read_text())
    validate_source(source, members, enum_value)
    if args.self_test:
        self_test(source, members, enum_value)
    print(f"Native portability source matches pinned vk.xml: {len(members) - 2} feature bits, sType {enum_value}")
    print("Source/schema checks only; native compilation and runtime checks are separate CI requirements.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as error:
        raise SystemExit(str(error))
