# testy-pytest-adapter

Pytest-плагин, который после прогона отправляет результаты автотестов в **TestY TMS**:
в нужный тест-план, рядом с ручными тестами, с историей запусков и связкой «кейс ↔ автотест».

## Зачем это нужно

Обычно автотесты и TMS живут отдельно. Результаты остаются в Allure или JUnit, каждый новый
прогон перетирает предыдущий, а статусы в TMS приходится обновлять руками.

Этот адаптер решает эту проблему:

- сопоставляет pytest-тест с тест-кейсом в TestY и отправляет результат
  (`passed`, `failed`, `skipped`, `broken`) с комментарием и трейсбеком;
- записывает результаты в конкретный тест-план и сохраняет историю прогонов;
- может создавать недостающие кейсы, наборы и планы из кода по разметке Allure;
- добавляет к результату логи и ссылки на CI-джобу.

Это именно мост между автотестами и TMS, а не замена Allure. Allure по-прежнему можно
использовать для подробного разбора прогона.

## Установка

```bash
pip install testy-pytest-adapter
````

Плагин подхватывается pytest автоматически. Пока не передан флаг `--testy`
или не включён `TESTY_ENABLED` / `testy_enabled`, он ничего не делает.

Вот инструкция, оформленная с отдельными блоками кода для копирования:

## Быстрый старт

### 1. Установите плагин
```bash
pip install testy-pytest-adapter
```

### 2. Настройте pytest.ini
Добавьте в `pytest.ini` (или в секцию `[tool.pytest.ini_options]` файла `pyproject.toml`):

```ini
[pytest]
; Параметр включающий плагин по умолчанию, любой запуск теста будет
; отправлять результат в TestY при наличии токена в окружении
testy_enabled = true 
testy_url = https://testy.example.ru
testy_project_id = 4
testy_root_name = Autotests

; маппинг статусов на значения вашего проекта TestY
testy_status_passed = Пройден
testy_status_failed = Провален
testy_status_skipped = Пропущен
testy_status_broken = Сломан
testy_status_untested = Untested
```

Токен доступа **не храните в файле** – передавайте его через переменную окружения `TESTY_TOKEN`.

### 3. Создайте структуру в TestY (первый запуск)
Синхронизация создаст дерево наборов, планов и тест‑кейсы с атрибутом `automation_id`.  
Тесты **не выполняются**, только собираются (`--collect-only`).

```bash
export TESTY_TOKEN=<ваш_токен>
pytest --testy --testy-sync --collect-only tests/
```

После синхронизации в TestY появится готовая структура.

### 4. Запускайте тесты с отправкой результатов
Для обычного прогона (в том числе в CI) флаги `--testy-sync` и `--collect-only` **не нужны**.

```bash
pytest --testy tests/
```
Результаты будут записаны в соответствующий тест‑план. При падениях к кейсу автоматически добавятся сообщение об ошибке, трейсбек, вложения и ссылки на CI-джобу.

## Как сопоставляются тест и кейс

Адаптер берёт стабильный идентификатор теста из pytest **nodeid**.
По умолчанию суффикс параметризации `[...]` отрезается.

Пример nodeid:

```text
tests/api/login_test.py::TestLogin::test_login
```

Дальше адаптер ищет кейс, у которого в атрибутах есть `automation_id` с таким же значением:

```text
cases/?case_attributes={"automation_id":"..."}
```

То есть id кейса в коде прописывать не нужно. Достаточно, чтобы у кейса в TestY был атрибут
`automation_id` с nodeid теста.

Заполнить этот атрибут можно вручную или автоматически через `--testy-sync`.
Название ключа настраивается через `testy_automation_key`.

По умолчанию адаптер находит нужный `Тест` так: резолвит кейс по `automation_id`, а затем
ищет его экземпляр в дереве плана `TESTY_PLAN_ID` (с учётом вложенных планов). Если же задан
`TESTY_TEST_MAP` (его передаёт `testy-gitlab-plugin`), адаптер пишет результат **напрямую**
в перечисленные там `test_id`, без резолва плана. Это позволяет одним прогоном записать
результаты в тесты из разных тест-планов и адресно — в конкретные `Тесты`, а не «угадывая» по
дереву. В этом режиме `TESTY_PLAN_ID`/`testy_root_name` для репортинга не нужны.

## Конфигурация

Значения берутся в таком порядке:

```text
CLI → переменные окружения → pytest.ini → дефолт
```

Токен намеренно не читается из `pytest.ini`. Его лучше передавать через переменную окружения
или CLI:

```text
--testy-token / TESTY_TOKEN
```

Несекретные настройки удобно хранить в `pytest.ini` или в `[tool.pytest.ini_options]`
в `pyproject.toml`.

```ini
[pytest]
testy_enabled = true
testy_url = https://testy.example.com
testy_project_id = 4
testy_root_name = Autotests

; Если вы используете самоподписанный сертификат на стенде:
testy_insecure = true

; имена статусов в проекте могут быть локализованы:
testy_status_passed = Пройден
testy_status_failed = Провален
testy_status_skipped = Пропущен
testy_status_broken = Сломан
testy_status_untested = Untested
```

Тогда для запуска достаточно передать токен через окружение:

```bash
TESTY_TOKEN=... pytest tests/
```

## Основные опции

| CLI                 | Переменная окружения                      | pytest.ini             | Назначение                                                             |
| ------------------- |-------------------------------------------|------------------------|------------------------------------------------------------------------|
| `--testy`           | `TESTY_ENABLED`                           | `testy_enabled`        | включить отправку в TestY                                              |
| `--testy-url`       | `TESTY_URL`                               | `testy_url`            | базовый адрес TestY                                                    |
| `--testy-token`     | `TESTY_TOKEN`                             | -                      | токен доступа                                                          |
| `--testy-project`   | `TESTY_PROJECT_ID`                        | `testy_project_id`     | id проекта                                                             |
| `--testy-plan`      | `TESTY_PLAN_ID`                           | `testy_plan_id`        | id корневого тест-плана                                                |
| -                   | `TESTY_TEST_MAP`                          | -                      | карта `{automation_id: [id тестов]}` для адресной записи результатов   |
| `--testy-root-name` | `TESTY_ROOT_NAME`                         | `testy_root_name`      | имя корня, если план нужно найти или создать по имени                  |
| `--testy-suite`     | `TESTY_SUITE_ID`                          | `testy_suite_id`       | корневой набор для автосоздания кейсов                                 |
| `--testy-sync`      | `TESTY_SYNC` или `TESTY_MODE=sync`        | -                      | создать кейсы и структуру перед прогоном                               |
| -                   | `TESTY_AUTOMATION_KEY`                    | `testy_automation_key` | ключ атрибута для матчинга, по умолчанию `automation_id`               |
| -                   | `TESTY_KEEP_PARAMS`                       | `testy_keep_params`    | не срезать `[param]`, каждая параметризация будет отдельным кейсом     |
| -                   | `TESTY_OVERRIDE_CASES`                    | `testy_override_cases` | перезаписывать шаги существующего кейса, по умолчанию `true`           |
| -                   | `TESTY_ATTACH`                            | `testy_attach`         | когда загружать вложения: `failure`, `always`, `never`                 |
| -                   | `TESTY_ATTACH_BYTES`                      | `testy_attach_bytes`   | захватывать `allure.attach` из памяти (скриншоты), по умолчанию `true` |
| -                   | `TESTY_AUTH_SCHEME`                       | `testy_auth_scheme`    | схема авторизации: `Token` или `Bearer`                                |
| -                   | `TESTY_INSECURE`                          | `testy_insecure`       | отключить проверку TLS-сертификата                                     |
| -                   | `TESTY_STATUS_PASSED` и остальные статусы | `testy_status_*`       | реальные имена статусов проекта                                        |
| -                   | `TESTY_WORKERS`                           | `testy_workers`        | Количество воркеров клиента для Testy API                              |

## Авторизация и статусы

Для авторизации используется TTL-токен из TestY:

```text
POST /api/token/obtain/
```

Имена статусов в TestY могут отличаться от стандартных. Например, в проекте они могут
называться `Пройден`, `Провален`, `Пропущен`.

Адаптер сначала приводит результат pytest к одному из канонических статусов:

```text
Passed / Failed / Skipped / Broken / Untested
```

Затем эти статусы можно замапить на реальные имена проекта:

```bash
TESTY_STATUS_PASSED=Пройден
TESTY_STATUS_FAILED=Провален
```

Или задать их в `pytest.ini`:

```ini
testy_status_passed = Пройден
testy_status_failed = Провален
```

## Автосоздание кейсов и структуры

Чтобы не заполнять `automation_id` вручную, можно запустить отдельный sync-шаг.
Он создаст недостающие кейсы и добавит их в план.

Тесты при этом не выполняются, запуск идёт через `--collect-only`:

```bash
pytest --collect-only --testy --testy-sync \
  --testy-root-name=Autotests \
  --testy-url="$TESTY_URL" \
  --testy-token="$TESTY_TOKEN" \
  --testy-project=4
```

Sync-шаг можно включить не только флагом `--testy-sync`, но и переменной окружения —
`TESTY_SYNC=1` или `TESTY_MODE=sync`. Это нужно для запуска из CI без правки команды pytest:
например, `testy-gitlab-plugin` при нажатии «Sync autotests» сам выставляет `TESTY_MODE=sync`.
Джоба синхронизации должна по-прежнему собирать тесты без выполнения (`--collect-only`).

Дерево суитов и тест-планов в TestY строится из разметки Allure:

| Allure в коде                                                                            | В TestY                                              |
| ---------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `@allure.parent_suite("API")` / `@allure.suite("/api/login")` / `@allure.sub_suite(...)` | вложенное дерево `TestSuite` и `TestPlan` под корнем |
| `@allure.title("GET /api/login")`                                                        | имя `TestCase`                                       |

Без Allure тоже работает. Allure не обязателен и даже не должен быть установлен.

Если у теста нет `@allure.title`, имя кейса берётся из имени pytest-теста:

```text
test_login
test_login[case1]
```

Если нет `@allure.suite`, `@allure.parent_suite` или `@allure.sub_suite`, дерево не строится.
Кейсы создаются плоско под корнем, который задан через `--testy-suite` или `--testy-root-name`.

Матчинг при этом всё равно работает по nodeid → `automation_id`.
Allure нужен только для более читаемых имён и структуры.

Sync идемпотентный: повторный запуск находит уже созданные сущности и не плодит дубли.
Лучше запускать его отдельным шагом, без xdist-воркеров, а уже после этого запускать обычный прогон
с отправкой результатов.

## Доказательства падений: вложения и ссылки

Вложения к результату можно добавить двумя способами.

**Через `testy.attach`** — явно указать локальный файл:

```python
import testy

def test_login(page):
    page.screenshot(path="fail.png")
    testy.attach("fail.png")
    ...
```

**Через Allure** — если установлен `allure-pytest`, адаптер сам подхватывает любые
`allure.attach(...)` и `allure.attach.file(...)` и грузит их к результату. Отдельно
вызывать `testy.attach` не нужно — в том числе если скриншот прикрепляется из хука
`pytest_runtest_makereport` (автоскриншот на падении):

```python
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and not report.passed:
        allure.attach(page.screenshot(), attachment_type=allure.attachment_type.PNG)
```

Картинки (`image/*`) дополнительно встраиваются прямо в текст комментария результата.

Когда загружать вложения, задаётся через `TESTY_ATTACH`:

```text
failure / always / never
```

По умолчанию используется `failure`, то есть вложения отправляются только на падениях.

Захват `allure.attach()` можно отключить, оставив только файловые вложения:

```text
TESTY_ATTACH_BYTES=false   # или testy_attach_bytes = false в pytest.ini
```

Также адаптер сохраняет в атрибутах результата ссылки из GitLab CI:

```text
CI_PIPELINE_URL
CI_JOB_URL
```

## Шаги Allure

Если установлен `allure-pytest`, адаптер забирает дерево выполненных `allure.step`
и синхронизирует его в кейс как структурные шаги.

Перезапись шагов существующего кейса управляется настройкой:

```text
testy_override_cases
```

По умолчанию значение `true`: кейсы поддерживаются в актуальном состоянии относительно кода.

Если Allure не установлен, этот механизм просто не используется.

## Параметризованные тесты

По умолчанию все варианты одного параметризованного теста схлопываются в один кейс.
Суффикс параметризации `[...]` отрезается.

Итоговый статус выбирается как худший из всех вариантов. Например, если один вариант упал,
весь кейс будет отмечен как `failed`. В комментарий добавляется краткая сводка.

Если нужно, чтобы каждый параметр стал отдельным кейсом, включите:

```bash
TESTY_KEEP_PARAMS=1
```

## Пример для GitLab CI

Если у вас уже заданы параметры в `pytest.ini`, то для запуска из Gitlab CI используйте такой конфиг:

```yaml
testy_run:
  stage: test
  script:
    - - pytest -v -s tests --testy-token=${TESTY_TOKEN}
```

`TESTY_*` удобно передавать как переменные пайплайна.
`TESTY_TOKEN` лучше хранить как masked CI/CD-переменную репозитория.

## Поведение и гарантии

* Ошибки отправки результатов только логируются.
* Прогон тестов не падает из-за проблем с TestY.
* При запуске через `pytest-xdist` воркеры собирают результаты, а запись в TestY выполняет
  только контроллер в конце прогона.

## Лицензия

MIT - см. файл [`LICENSE`](./LICENSE).
