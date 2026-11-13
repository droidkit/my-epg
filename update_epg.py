#!/usr/bin/env python3
"""Генератор EPG для IPTV-приложения.

Читает mapping.json (список нужных каналов) и epg_sources.json
(список публичных EPG-источников), скачивает их, объединяет
и оставляет только программы для нужных каналов.
Результат: epg.xml в корне репозитория.
"""

import copy
import gzip
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

MAPPING_FILE = "mapping.json"
SOURCES_FILE = "epg_sources.json"
OUTPUT_FILE = "epg.xml"


def load_json(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)


def strip_ns(tag):
    """Убирает namespace из имени тега: '{http://...}channel' -> 'channel'."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def normalize_name(name):
    """Та же нормализация, что в приложении: lowercase, скобки,
    суффиксы качества, гомоглифы кириллица -> латиница."""
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = re.sub(r"\s*\[[^\]]*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suffix in (" full hd", " fhd", " uhd", " hd", " sd", " 4k"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    homoglyphs = {
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
        "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u043a": "k",
        "\u043c": "m", "\u0442": "t",
    }
    return "".join(homoglyphs.get(ch, ch) for ch in s)


def download(url):
    print("Скачиваем: " + url)
    try:
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        content = resp.content
        if content[:2] == b"\x1f\x8b":
            print("  gzip, распаковываем...")
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
                content = gz.read()
        print("  Получено {} байт".format(len(content)))
        return content
    except Exception as e:
        print("  Ошибка: {}".format(e))
        return None


def parse_xmltv(xml_content):
    """Возвращает (programmes, channels_by_id, channels_by_name_norm)."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print("  Ошибка XML: {}".format(e))
        return {}, {}, {}

    channels_by_id = {}
    channels_by_name = {}
    programmes = {}

    for el in root:
        local = strip_ns(el.tag)
        if local == "channel":
            ch_id = el.get("id")
            if ch_id:
                channels_by_id[ch_id] = el
                for child in el:
                    if strip_ns(child.tag) == "display-name" and child.text:
                        key = normalize_name(child.text)
                        if key and key not in channels_by_name:
                            channels_by_name[key] = ch_id
        elif local == "programme":
            ch_id = el.get("channel")
            if ch_id:
                programmes.setdefault(ch_id, []).append(el)

    print("  Каналов: {}, программ: {}".format(
        len(channels_by_id), sum(len(p) for p in programmes.values())
    ))
    return programmes, channels_by_id, channels_by_name


def main():
    print("=== Начинаем генерацию EPG ===")
    mapping = load_json(MAPPING_FILE)
    sources = load_json(SOURCES_FILE)
    print("Нужно каналов: {}".format(len(mapping)))

    final_channels = {}
    final_programmes = {}
    matched_log = []

    for source in sources:
        print("\n--- Источник: {} ---".format(source.get("name", source["url"])))
        content = download(source["url"])
        if not content:
            continue

        programmes, channels_by_id, channels_by_name = parse_xmltv(content)

        for item in mapping:
            our_id = item["tvg_id"]
            our_name = item["name"]
            our_norm = normalize_name(our_name)

            src_id = None
            match_mode = None
            if our_id in channels_by_id:
                src_id = our_id
                match_mode = "by tvg_id"
            elif our_norm in channels_by_name:
                src_id = channels_by_name[our_norm]
                match_mode = "by name"

            if not src_id:
                continue

            if our_id not in final_channels:
                ch = ET.Element("channel")
                ch.set("id", our_id)
                src_ch = channels_by_id.get(src_id)
                if src_ch is not None:
                    for child in src_ch:
                        if strip_ns(child.tag) == "display-name":
                            dn = ET.SubElement(ch, "display-name")
                            dn.text = child.text
                if len(ch) == 0:
                    dn = ET.SubElement(ch, "display-name")
                    dn.text = our_name
                final_channels[our_id] = ch
                matched_log.append("{} <- {} ({})".format(our_id, src_id, match_mode))

            if src_id in programmes and our_id not in final_programmes:
                final_programmes[our_id] = []
                for prog in programmes[src_id]:
                    new_prog = copy.deepcopy(prog)
                    new_prog.set("channel", our_id)
                    final_programmes[our_id].append(new_prog)

    if not final_channels:
        print("\n!!! Не найдено ни одного канала. Проверьте mapping.json и источники.")
        sys.exit(1)

    print("\n=== Собираем итоговый XML ===")
    root = ET.Element("tv")
    root.set("generator-info-name", "Custom EPG for IPTV App")

    for ch_id in sorted(final_channels.keys()):
        root.append(final_channels[ch_id])

    total_progs = 0
    for ch_id in sorted(final_programmes.keys()):
        progs = final_programmes[ch_id]
        progs.sort(key=lambda p: p.get("start", ""))
        for prog in progs:
            root.append(prog)
            total_progs += 1

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)

    print("\n=== Готово! ===")
    print("Каналов: {}".format(len(final_channels)))
    print("Программ: {}".format(total_progs))
    print("Файл: {}".format(Path(OUTPUT_FILE).absolute()))
    print("\nСматченные каналы:")
    for m in matched_log:
        print("  " + m)


if __name__ == "__main__":
    main()
