# Aftertime--ENTERPRICE: ядро, пакети моделей і налаштування

Цей репозиторій публічний і містить **лише механізми**: код Model Lab, редактор BPMN,
синхронізацію BPMN ↔ SVG, міст до зовнішнього центру E2E, звіти тестів і запуск.
Предметні моделі підключаються **пакетами моделей**; адреси й шляхи стендів лежать в окремому
приватному репозиторії налаштувань.

## Репозиторії

| Репозиторій | Видимість | Вміст | Локально |
|---|---|---|---|
| `Aftertime--ENTERPRICE` | публічний | ядро: механізми, тести, приклади | `<root>/Aftertime` |
| `Aftertime-model-energy` | публічний | пакет: модель енергосистеми | `<root>/Aftertime-model-energy` |
| `Aftertime-model-rental` | приватний | пакет: процеси прокату, прив'язки до тестів | `<root>/Aftertime-model-rental` |
| `Aftertime-config--ENTERPRICE` | приватний | налаштування стендів, ТЗ | `<root>/Aftertime-config` |

`<root>` — спільна тека розгортання (наприклад, `D:\Aftertime`).

## Пакети моделей

Пакет — тека з маніфестом `aftertime-pack.json`:

```json
{
  "id": "rental",
  "title": "Прокат спорядження",
  "version": "1.0.0",
  "core": ">=0.4",
  "description": "...",
  "processes": "processes/*.bpmn",
  "bindings": "e2e/bindings.json",
  "installer": "pack_install.py",
  "publish": true
}
```

- `processes` — BPMN-процеси (і синхронні `*.svg`); ядро імпортує їх і, якщо `publish`,
  публікує. Процес належить пакету (`lab_workflows.pack_id`).
- `bindings` — прив'язки процесів до E2E-кейсів: `title`, `phases`, `runner.cases`.
- `installer` — необов'язковий Python-модуль з `install(db, pack)` для моделей систем
  (компоненти, граф, сценарії); проєкти позначаються `lab_projects.pack_id`.
- Ядро шукає пакети в `AFTERTIME_PACKS_DIR` (кожна підтека з маніфестом). Docker Compose
  монтує її в `/packs` (лише читання); шлях на хості — у `platform/.env`, наприклад
  `AFTERTIME_PACKS_DIR=../..`. Без нього монтується `platform/packs.example/`.
- Під час старту API (`python -m app.pack_install`) пакети реєструються, інсталятори
  виконуються, процеси імпортуються. Повторне встановлення нічого не дублює; зламаний пакет
  не заважає старту. API: `GET /lab/packs`, `POST /lab/packs/{id}/install`, `PATCH /lab/packs/{id}`.
- В інтерфейсі перемикач «Пакет моделі» (контур моделювання) фільтрує проєкти й процеси.
  Під час відкриття показується контур з `aftertime.json` (`GET /lab/settings`); вибір у вкладці
  браузера діє до її закриття. Контур без моделі системи відкривається одразу на своєму процесі,
  на всю ширину, без панелей симулятора.
- `*.bpmn` і `*.svg` зберігаються без перетворення кінців рядків (`.gitattributes`): SVG містить
  SHA-256 свого BPMN.

## Контракт налаштувань

Механізми читають налаштування стенда з теки `AFTERTIME_CONFIG_DIR`:

```text
Aftertime-config/
  aftertime.json       контур (defaultPack) і процес (defaultProcess), які інтерфейс відкриває першими
  e2e/bindings.json    перевизначення прив'язок для цього стенда (runner: шаблони доказів, live …)
  e2e/bridge.env       адреса центру E2E, шляхи до тек запусків і результатів, PACKS_DIR
  e2e/live-proofs.ps1  команда живого читання доказів
```

- Прив'язки = прив'язки пакетів + `e2e/bindings.json` поверх них (`runner` зливається по ключах).
  Так само їх об'єднує міст E2E.
- Docker Compose монтує теку як `/config` (лише читання); без `AFTERTIME_CONFIG_DIR` у
  `platform/.env` монтується `platform/config.example/`.
- Тести механізмів не залежать від приватних налаштувань і пакетів організації.

## Гілки

- `main` — розгортання; `DEVE` — розробка. Злиття лише `DEVE` → `main`.
- Оригінальний репозиторій `AftertimeINFO/Aftertime` підключено як `upstream`; зміни з його
  `main` зливаються в `DEVE`.
- Пакети й репозиторій налаштувань мають ті самі дві гілки.
