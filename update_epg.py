#!/usr/bin/env python3
"""Автоматический генератор EPG для IPTV-приложения droidkit/IPTV.

Источник каналов: playlist.m3u.txt из репозитория приложения.
Выход: epg.xml в корне репозитория.

ВАЖНО про приоритет источников:
  epg_sources.json должен быть УПОРЯДОЧЕН по убыванию доверия.
  Первый источник — самый надёжный (официальный сайт канала).
  Последние — «затычки», закрывающие дыры в сетке.
  Слоты (start..stop) занимаются программами по порядку источников:
  если источник[0] дал программу на 10:00–11:00, то программа
  источника[1] на 10:30 уже НЕ попадёт в epg.xml.
"""

import copy
import gzip
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
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
        resp = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
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
# Время XMLTV → epoch-секунды (с учётом часового пояса)
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?$")


def parse_xmltv_time(s: str) -> int | None:
    """XMLTV-время → epoch-секунды. Поддерживает 'YYYYMMDDhhmmss[ ±zzzz]'.

    Без явного пояса по спецификации XMLTV подразумевается UTC.
    """
    if not s:
        return None
    m = _TIME_RE.match(s.strip())
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    tz_str = m.group(2)
    if tz_str:
        sign = 1 if tz_str[0] == "+" else -1
        hours, minutes = int(tz_str[1:3]), int(tz_str[3:5])
        tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


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

    # 2. Источники (упорядочены по убыванию приоритета)
    sources = json.loads(Path(SOURCES_FILE).read_text(encoding="utf-8"))
    if not isinstance(sources, list) or not sources:
        print("!!! epg_sources.json пуст или не является массивом")
        sys.exit(1)

    # final: our_id -> {
    #   "name": str,
    #   "programmes": [(priority_idx, prog_el, start_sec, stop_sec), ...]
    # }
    final: dict[str, dict] = {}

    # 3. Собираем программы из всех источников с меткой приоритета
    for src_idx, src in enumerate(sources):
        print(f"\n--- Источник [{src_idx}]: {src.get('name', src.get('url'))} ---")
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

            final.setdefault(our_id, {"name": our_name, "programmes": []})

            for prog in programmes_by_id.get(src_id, []):
                start_sec = parse_xmltv_time(prog.get("start", ""))
                stop_sec = parse_xmltv_time(prog.get("stop", ""))
                if start_sec is None or stop_sec is None or stop_sec <= start_sec:
                    continue
                final[our_id]["programmes"].append(
                    (src_idx, copy.deepcopy(prog), start_sec, stop_sec)
                )

            if mode:
                print(f"  {our_name!r} -> {src_id!r} ({mode})")

    # 4. Сборка epg.xml с приоритетом источников и отсечением пересечений
    print("\n=== Сборка epg.xml ===")
    root = ET.Element("tv")
    root.set("generator-info-name", "Custom EPG for IPTV App")

    now_sec = int(time.time())
    one_day_sec = 24 * 60 * 60

    total_progs = 0
    used_ids = set()

    for our_id, data in sorted(final.items()):
        # Сортируем по (приоритет источника, начало).
        # Источник[0] обрабатывается первым → его программы занимают слоты,
        # всё, что с ними пересекается, отбрасывается.
        items = sorted(data["programmes"], key=lambda x: (x[0], x[2]))

        kept = []
        for priority, prog, start, stop in items:
            if stop < now_sec - one_day_sec:
                continue
            # ★ ПРАВИЛЬНАЯ проверка пересечения интервалов [start, stop):
            #   пересекаются ⇔ new.start < existing.stop AND new.stop > existing.start
            if any(start < ke and stop > ks for _, _, ks, ke in kept):
                continue
            kept.append((priority, prog, start, stop))

        if not kept:
            continue

        ch_el = ET.SubElement(root, "channel")
        ch_el.set("id", our_id)
        dn = ET.SubElement(ch_el, "display-name")
        dn.text = data["name"]

        for _, prog, _, _ in kept:
            prog.set("channel", our_id)
            root.append(prog)
            total_progs += 1
        used_ids.add(our_id)

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)

    # 5. Отчёт
    print("\n=== Готово ===")
    print(f"Каналов с программами: {len(used_ids)}")
    print(f"Программ: {total_progs}")
    print(f"Файл: {Path(OUTPUT_FILE).absolute()}")

    if len(used_ids) < 5 or total_progs < 100:
        print("!!! Слишком мало данных, результат подозрительный")
        sys.exit(1)


if __name__ == "__main__":
    main()
