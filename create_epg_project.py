#!/usr/bin/env python3
"""Создаёт структуру my-epg/ с готовыми файлами для GitHub Actions.

Запуск:
    python create_epg_project.py

После запуска папка my-epg/ содержит:
    .github/workflows/update-epg.yml
    mapping.json
    epg_sources.json
    update_epg.py
    requirements.txt
    README.md
"""

from pathlib import Path

ROOT = Path("my-epg")

FILES = {
    "requirements.txt": r"""requests>=2.31.0
""",

    "epg_sources.json": r"""[
  {
    "name": "epg.one (lite)",
    "url": "http://epg.one/epg2.xml"
  },
  {
    "name": "iptvx.one (lite)",
    "url": "https://iptvx.one/epg/epg_lite.xml.gz"
  }
]
""",

    "mapping.json": r"""[
  { "name": "2х2", "tvg_id": "2x2.ru@SD" },
  { "name": "Восток 24", "tvg_id": "Vostok24.ru@SD" },
  { "name": "Домашний", "tvg_id": "Domashniy.ru@SD" },
  { "name": "Звезда", "tvg_id": "Zvezda.ru@SD" },
  { "name": "Карусель", "tvg_id": "Carousel.ru@SD" },
  { "name": "Кубань 24", "tvg_id": "Kuban24.ru@SD" },
  { "name": "Россия К (Культура)", "tvg_id": "RussiaK.ru@SD" },
  { "name": "Лен ТВ 24", "tvg_id": "LenTV24.ru@SD" },
  { "name": "Мир", "tvg_id": "Mir.ru@SD" },
  { "name": "Мир +7", "tvg_id": "Mir.ru@Plus7" },
  { "name": "Муз ТВ", "tvg_id": "MuzTV.ru@SD" },
  { "name": "Новгородское областное телевидение", "tvg_id": "NovgorOblTV.ru@HD" },
  { "name": "НТВ", "tvg_id": "NTV.ru@SD" },
  { "name": "НТК 21", "tvg_id": "NTK21.ru@HD" },
  { "name": "ОТР", "tvg_id": "OTR.ru@SD" },
  { "name": "Первый канал", "tvg_id": "ChannelOne.ru@SD" },
  { "name": "Прима", "tvg_id": "Prima.ru@SD" },
  { "name": "Пятница!", "tvg_id": "Friday.ru@SD" },
  { "name": "Пятый Канал", "tvg_id": "Channel5.ru@SD" },
  { "name": "РЕН ТВ", "tvg_id": "RENTV.ru@SD" },
  { "name": "Россия 1", "tvg_id": "Russia1.ru@SD" },
  { "name": "Россия 24", "tvg_id": "Russia24.ru@SD" },
  { "name": "Россия К", "tvg_id": "RussiaK.ru@SD" },
  { "name": "Санкт-Петербург", "tvg_id": "SanktPeterburg.ru@SD" },
  { "name": "Солнце", "tvg_id": "Solnce.ru@SD" },
  { "name": "Спас", "tvg_id": "Spas.ru@SD" },
  { "name": "СТС", "tvg_id": "STS.ru@SD" },
  { "name": "СТС Love", "tvg_id": "STSLove.ru@SD" },
  { "name": "Суббота!", "tvg_id": "Subbota.ru@SD" },
  { "name": "ТВ Центр", "tvg_id": "TVCentr.ru@SD" },
  { "name": "ТВ-3", "tvg_id": "TV3.ru@SD" },
  { "name": "ТНТ", "tvg_id": "TNT.ru@SD" },
  { "name": "ТНТ4", "tvg_id": "TNT4.ru@SD" },
  { "name": "Че!", "tvg_id": "Che.ru@SD" },
  { "name": "Ю", "tvg_id": "U.ru@SD" }
]
""",

    "update_epg.py": r'''#!/usr/bin/env python3
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
''',

    ".github/workflows/update-epg.yml": r"""name: Update EPG

on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run EPG generator
        run: python update_epg.py

      - name: Commit and push
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add epg.xml
          git diff --staged --quiet || git commit -m "Update EPG [skip ci]"
          git push
""",

    "README.md": r"""# my-epg

Автоматически обновляемый EPG только для каналов из `playlist.m3u.txt` приложения IPTV.

## Что здесь

- `mapping.json` — список 35 каналов (tvg_id + имя).
- `epg_sources.json` — публичные EPG-источники.
- `update_epg.py` — скрипт объединения и фильтрации.
- `.github/workflows/update-epg.yml` — GitHub Actions, обновляет `epg.xml` раз в сутки.

## Первый запуск

1. Actions → Update EPG → Run workflow.
2. Через 1-2 минуты в корне появится `epg.xml`.
3. Откройте `epg.xml` → Raw → скопируйте URL:
   `https://raw.githubusercontent.com/ВАШ_НИК/my-epg/main/epg.xml`
4. В приложении IPTV: Настройки → EPG → вставьте URL → Сохранить.

## Обновление mapping.json

Если добавили канал в плейлист — добавьте строку в `mapping.json`:
```json
{ "name": "Название", "tvg_id": "tvg.id" }
```
Commit, GitHub Actions пересоберёт `epg.xml` в ближайший запуск.
""",
}


def main():
    print("Создаю структуру в: {}".format(ROOT.absolute()))
    for rel_path, content in FILES.items():
        target = ROOT / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        print("  + {}".format(rel_path))
    print("\nГотово. Создано файлов: {}".format(len(FILES)))
    print("\nСледующий шаг:")
    print("  1. Создайте публичный репозиторий 'my-epg' на GitHub")
    print("  2. Загрузите содержимое папки my-epg/ (все файлы + папку .github/)")
    print("  3. Actions → Update EPG → Run workflow")
    print("  4. Через 1-2 мин в корне появится epg.xml")


if __name__ == "__main__":
    main()