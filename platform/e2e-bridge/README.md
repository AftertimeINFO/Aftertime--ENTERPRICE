# e2e-bridge

Міст між процесами BPMN в Aftertime і зовнішнім центром керування E2E-тестами. Він
запускається на хості, де працює центр, і використовує лише стандартну бібліотеку Python 3.11+.

Міст не запускає тести сам. Він ставить кейс у чергу центру (`POST /api/run`), читає журнал
завдання й докази фаз, а потім перетворює їх на стани фаз процесу:
`passed`, `failed`, `active`, `pending`, `not-reached`, `stopped`.

## Налаштування

Налаштування лежать у `$AFTERTIME_CONFIG_DIR/e2e/`. Без цієї змінної міст шукає
`../Aftertime-config` поруч із коренем репозиторію.

- `bindings.json` — прив'язки процесів; секція `runner` кожної прив'язки містить:
  - `cases` — кейси центру;
  - `runFolderMask` — маска теки запуску в `RUN_ROOT`;
  - `proofPattern`, `errorPattern` — регулярні вирази імен файлів доказів з групою `phase`;
  - `live` — чи читати докази під час прогону.
- `bridge.env`:
  - `CENTER_URL` — адреса центру;
  - `RUN_ROOT` — тека, куди центр складає теки запусків;
  - `LIVE_COMMAND` — команда, що друкує JSON-список `{name, text}` поточних доказів
    (отримує `BRIDGE_SINCE`);
  - `POLL_SECONDS`, `LIVE_SECONDS` — інтервали опитування.

## Команди

```bash
python -m e2e_bridge status                      # стан центру: що виконується, черга
python -m e2e_bridge replay BINDING RUN_FOLDER   # стани фаз за записаною текою запуску
python -m e2e_bridge run BINDING [--wait-free]   # поставити кейс у чергу й стежити
python -m e2e_bridge follow BINDING JOB_ID       # стежити за вже поставленим завданням
python -m e2e_bridge serve [--simulate]          # виконувати прогони, запитані в Aftertime
python -m e2e_bridge import-results FOLDER...    # теки allure-results → звіти Aftertime
python -m e2e_bridge sync-results [ROOT]         # усі запуски з RESULTS_ROOT
```

## Екран машини під час прогону

`SCREEN_COMMAND` у `bridge.env` — команда з `-Action start|frame|stop`; `frame` пише останній
JPEG у файл `BRIDGE_FRAME_OUT`. Міст запускає захоплення на старті прогону, раз на
`SCREEN_SECONDS` (типово 3) передає кадр в Aftertime (`POST /lab/e2e-runs/{id}/frames`), а після
завершення робить останній кадр і зупиняє захоплення. Aftertime записує з кадром поточну фазу й
не зберігає однакові кадри поспіль.

Інструменти в `tools/`:

- `session-screen.ps1` — захоплення екрана інтерактивного сеансу Windows зсередини машини
  (лише читає екран); для машин, де тести працюють у віддаленому чи розширеному сеансі;
- `hyperv-screen.ps1` — знімок консолі VM Hyper-V з хоста, без змін у VM; не бачить розширених
  (RDP/VMBus) сеансів, тож підходить, коли тести працюють у консолі.

`bridge.env` додатково має `AFTERTIME_API_URL` (API Aftertime) і `RESULTS_ROOT` (тека з
підтеками `allure-results`). Після прогону з Aftertime міст сам імпортує результати цього
прогону у звіти. Підтеки з `_` на початку (злиті подання) не імпортуються.

Кожна подія — окремий рядок JSON: `queued`, `states`, `status`, `log`, `phase`, `finished`, `error`.

## Як визначаються фази

- **Фаза з доказом** отримує статус із доказу: поле `"verified": false` або файл помилки означає збій.
- **Фаза без доказу** вважається пройденою, якщо пізніша фаза має доказ.
- **Решту фаз** закриває результат кейсу:
  - `passed` — усі пройдені;
  - `failed` або `stopped` — наступна після останнього доказу фаза отримує цей статус, решта `not-reached`.
- **Живі докази**, які вже існували на старті, належать попереднім прогонам і ігноруються.

## Тести

```bash
cd platform/e2e-bridge
python -m pytest
```
