#!/usr/bin/env python3
"""Автоматический генератор EPG для IPTV-приложения droidkit/IPTV.

Источник каналов: playlist.m3u.txt из репозитория приложения.
Выход: epg.xml в корне репозитория.
"""

import copy
import gzip
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PLAYLIST_URL = "https://raw.githubusercontent.com/droidkit/IPTV/main/playlist.m3u.txt"
SOURCES_FILE = "epg_sources.json"
OUTPUT_FILE = "epg.xml"

# ---------------------------------------------------------------------------
# Нормализация имён — та же логика, что в XmlTvParser.normalizeChannelName()
# ---------------------------------------------------------------------------

_PARENS = re.compile(r"\s*\([^)]*\)")
_BRACKETS = re.compile(r"\s*\[[^\]]*\]")
_SPACES = re.compile(r"\s+")
_QUALITY_SUFFIXES = (" full hd", " fhd", " uhd", " hd", " sd", " 4k")
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "к": "k", "м": "m", "т": "t",
})


def normalize_name(name: str) -> str:
    if not name:
        return ""
    s = name.lower()
    s = _PARENS.sub("", s)
    s = _BRACKETS.sub("", s)
    s = _SPACES.sub(" ", s).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _QUALITY_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                changed = True
    return s.translate(_HOMOGLYPHS)


# ---------------------------------------------------------------------------
# M3U-парсер
# ---------------------------------------------------------------------------

def parse_playlist(text: str) -> list[dict]:
    """Возвращает список {tvg_id, name}. tvg_id может быть None."""
    channels = []
    current_id = None
    current_name = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            m_id = re.search(r'tvg-id="([^"]*)"', line)
            current_id = m_id.group(1) if m_id else None
            comma = line.find(",")
            current_name = line[comma + 1:].strip() if comma != -1 else ""
        elif line.startswith("http") and current_name is not None:
            channels.append({"tvg_id": current_id, "name": current_name})
            current_id = None
            current_name = None
    return channels


# ---------------------------------------------------------------------------
# Скачивание с автоопределением gzip
# ---------------------------------------------------------------------------

def download(url: str) -> bytes | None:
    print(f"Скачиваем: {url}")
    try:
        resp = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        content = resp.content
        if content[:2] == b"\x1f\x8b":
            print("  gzip, распаковываем...")
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
                content = gz.read()
        print(f"  Получено {len(content)} байт")
        return content
    except Exception as e:
        print(f"  Ошибка: {e}")
        return None


# ---------------------------------------------------------------------------
# XMLTV-парсер
# ---------------------------------------------------------------------------

def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def parse_xmltv(xml_content: bytes):
    """Возвращает (channels_by_id, channels_by_name_norm, programmes_by_id)."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print(f"  Ошибка XML: {e}")
        return {}, {}, {}

    channels_by_id = {}
    channels_by_name = {}
    programmes_by_id = {}

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
                programmes_by_id.setdefault(ch_id, []).append(el)

    print(f"  Каналов: {len(channels_by_id)}, "
          f"программ: {sum(len(p) for p in programmes_by_id.values())}")
    return channels_by_id, channels_by_name, programmes_by_id


# ---------------------------------------------------------------------------
# Основной сценарий
# ---------------------------------------------------------------------------

def main():
    print("=== Старт генерации EPG ===")

    # 1. Плейлист
    playlist_raw = download(PLAYLIST_URL)
    if not playlist_raw:
        print("!!! Не удалось скачать плейлист")
        sys.exit(1)
    playlist = parse_playlist(playlist_raw.decode("utf-8", errors="replace"))
    print(f"Каналов в плейлисте: {len(playlist)}")

    # 2. Источники
    sources = json.loads(Path(SOURCES_FILE).read_text(encoding="utf-8"))

    # final: our_id -> {"name": str, "programmes": [...]}
    final: dict[str, dict] = {}

    # 3. Обрабатываем каждый источник
    for src in sources:
        print(f"\n--- Источник: {src['name']} ---")
        content = download(src["url"])
        if not content:
            continue
        channels_by_id, channels_by_name, programmes_by_id = parse_xmltv(content)

        for ch in playlist:
            our_id = ch["tvg_id"] or f"name:{normalize_name(ch['name'])}"
            our_name = ch["name"]

            src_id = None
            mode = None
            if ch["tvg_id"] and ch["tvg_id"] in channels_by_id:
                src_id = ch["tvg_id"]
                mode = "by tvg_id"
            else:
                norm = normalize_name(our_name)
                if norm in channels_by_name:
                    src_id = channels_by_name[norm]
                    mode = "by name"

            if not src_id:
                continue

            if our_id not in final:
                final[our_id] = {"name": our_name, "programmes": []}

            # ★ Берём программы из КАЖДОГО источника, а не только из первого.
            for prog in programmes_by_id.get(src_id, []):
                final[our_id]["programmes"].append(copy.deepcopy(prog))

            if mode:
                print(f"  {our_name!r} -> {src_id!r} ({mode})")

    # 4. Собираем итоговый XML
    print("\n=== Сборка epg.xml ===")
    root = ET.Element("tv")
    root.set("generator-info-name", "Custom EPG for IPTV App")

    now_ms = int(time.time() * 1000)
    one_day_ms = 24 * 60 * 60 * 1000

    total_progs = 0
    used_ids = set()

    for our_id, data in sorted(final.items()):
        ch_el = ET.SubElement(root, "channel")
        ch_el.set("id", our_id)
        # ★ Наше имя — ПЕРВОЕ display-name, чтобы приложение точно сматчило
        dn = ET.SubElement(ch_el, "display-name")
        dn.text = data["name"]

        # Дедупликация программ по (start, stop, title)
        seen = set()
        valid = []
        for p in data["programmes"]:
            start = p.get("start", "")
            stop = p.get("stop", "")
            title = ""
            for child in p:
                if strip_ns(child.tag) == "title" and child.text:
                    title = child.text
                    break
            key = (start, stop, title)
            if key in seen:
                continue
            seen.add(key)

            # ★ Отбрасываем программы, которые уже закончились более суток назад
            # (start в формате XMLTV — YYYYMMDDhhmmss ±zzzz)
            try:
                start_ms = _xmltv_to_ms(start)
                if start_ms and start_ms < now_ms - one_day_ms:
                    continue
            except Exception:
                pass
            valid.append(p)

        if not valid:
            continue

        used_ids.add(our_id)
        for p in valid:
            p.set("channel", our_id)
            root.append(p)
            total_progs += 1

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)

    # 5. Валидация
    print("\n=== Готово ===")
    print(f"Каналов с программами: {len(used_ids)}")
    print(f"Программ: {total_progs}")
    print(f"Файл: {Path(OUTPUT_FILE).absolute()}")

    if len(used_ids) < 5 or total_progs < 100:
        print("!!! Слишком мало данных, результат подозрительный")
        sys.exit(1)


def _xmltv_to_ms(s: str) -> int | None:
    """Грубое преобразование XMLTV-даты в epoch-ms (для фильтрации)."""
    m = re.match(r"^(\d{14})", s or "")
    if not m:
        return None
    raw = m.group(1)
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


if __name__ == "__main__":
    main()
