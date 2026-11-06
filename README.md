# my-epg

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
