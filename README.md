# gpon_turon

Чистый рабочий GPON-проект (BDCOM) для поиска ONU, просмотра OLT/портов и базового управления через SNMP.

Этот README написан как контекст для нового ChatGPT-сеанса: чтобы быстро понять архитектуру, текущую логику и правила изменений.

## 1. Цель проекта

Приложение для NOC/техподдержки:
- хранит список OLT,
- обновляет кэш портов и ONU через SNMP,
- ищет ONU по SN,
- показывает состояние ONU и OLT,
- фиксирует последние новые ONU,
- дает безопасные управляющие действия (refresh, bounce port, reboot ONU).

Текущий приоритет: стабильность и предсказуемая логика, без «магии».

## 2. Стек и структура

- Backend: Flask
- DB: SQLite
- SNMP: `snmpbulkwalk`, `snmpset`
- Шаблоны: Jinja2
- Стиль: единый `static/style.css`

Структура:
- `src/gpon_turon/app.py` — создание Flask app, автообновление, jinja-фильтры
- `src/gpon_turon/routes/` — HTTP-роуты (`olts.py`, `onu.py`)
- `src/gpon_turon/services/` — бизнес-логика (SNMP, refresh, reboot)
- `src/gpon_turon/repositories/` — SQL и работа с таблицами
- `src/gpon_turon/db.py` — init БД и runtime-миграции
- `templates/` — страницы
- `static/` — css и картинки
- `schema.sql` — базовая схема
- `run.py` — запуск

Архитектурный принцип: `routes -> services -> repositories`.

## 3. Текущий функционал

### 3.1 Главная `/`
- Добавление/удаление OLT
- Кнопка `Обновить все OLT`
- Кнопка `Новые ONU`
- Таблица OLT (Hostname, IP, Vendor, Last refresh, действия)

### 3.2 Страница OLT `/olt/<ip>`
- Таблица GPON-портов с количеством ONU
- `Обновить данные` (refresh одного OLT)
- `Информация об OLT`
- Для каждого порта:
  - `Открыть`
  - `Перезагрузить порт` (SNMP set down/up)

### 3.3 Страница порта `/olt/<ip>/port/<ifindex>`
- ONU на порту, пагинация
- Переход на карточку ONU

### 3.4 Карточка ONU `/onu/sn/<sn>`
- OLT IP, Порт, Status, LAN статус, Distance, RX/TX, ONU vendor
- Последняя причина отключения
- Для OFFLINE: `Последний раз онлайн`
- Кнопка `Перезагрузить ONU` (красная, с подтверждением)

### 3.5 Страница «Новые ONU» `/onus/new`
- Показывает последние 50 новых ONU
- Колонки: №, SN, дата подключения, IP OLT, порт
- SN -> страница ONU, IP -> страница OLT
- Логика retention: запись остается в списке, пока не вытеснится 51-й новой

### 3.6 Страница инфо OLT `/olt/<ip>/info`
Показывает:
- IP
- Производитель
- Модель
- Версия прошивки
- Память
- CPU
- Температура

Если часть OID недоступна — выводится `-`.

## 4. Ключевая логика данных

### 4.1 Refresh OLT
Основной поток в `OltService.refresh_olt`:
1. Берет `ifName` и GPON bind через SNMP.
2. Синхронизирует `ponports` (diff insert/update/delete).
3. Синхронизирует `gpon` (diff insert/delete).
4. Удаляет кросс-OLT дубликаты ONU по SN (если ONU переехала на другой OLT/порт).
5. Обновляет `last_refresh_at`.

### 4.2 Последние новые ONU
При вставке новых SN в `gpon`:
- определяется, была ли SN раньше глобально известна,
- если нет — пишется в `recent_new_onu`,
- таблица подрезается до 50 записей.

### 4.3 Реальный `Последний раз онлайн`
Важно:
- `last_seen` — это «последний раз ONU видна в кэше», не реальный online.
- Для реального времени online используется `onu_seen.last_online`.
- `last_online` обновляется только если по SNMP статус ONU = `ONLINE`.
- На странице ONU при `OFFLINE` показывается именно `last_online`.

Это сделано, чтобы `OFFLINE` ONU не показывали время последнего автоrefresh как «последний онлайн».

### 4.4 Время (часовой пояс)
Все времена в UI выводятся через фильтр `tz_tashkent`:
- зона: `Asia/Tashkent` (GMT+5)
- источник в БД считается UTC

## 5. База данных (основные таблицы)

- `olts` — OLT (hostname/ip/community/vendor/last_refresh_at)
- `ponports` — порты OLT (ifindex, name)
- `gpon` — ONU привязки (olt_ip, portonu, idonu, snonu)
- `onu_seen` — история наблюдения SN:
  - `first_seen`
  - `last_seen`
  - `last_online`
  - `status`
- `recent_new_onu` — последние 50 новых ONU

Runtime-миграции в `db.py` добавляют недостающие колонки/таблицы для старых БД.

## 6. SNMP OID (используемые сейчас)

BDCOM/GPON ключевые OID:
- GPON bind SN: `1.3.6.1.4.1.3320.10.2.6.1.3`
- ONU SN table: `1.3.6.1.4.1.3320.10.3.1.1.4`
- ONU status: `1.3.6.1.4.1.3320.10.3.3.1.4`
- ONU RX/TX: `...10.3.4.1.2`, `...10.3.4.1.3`
- ONU distance: `...10.3.1.1.33`
- ONU last down reason: `...10.3.1.1.35`
- ONU reboot: `...10.3.2.1.4.<globIdx>`
- Port bounce (ifAdminStatus): `1.3.6.1.2.1.2.2.1.7.<ifIndex>`
- OLT sysDescr/sysName: `1.3.6.1.2.1.1.1.0`, `1.3.6.1.2.1.1.5.0`
- OLT CPU/MEM/TEMP:
  - `1.3.6.1.4.1.3320.9.109.1.1.1.1.0`
  - `1.3.6.1.4.1.3320.9.48.1.0`
  - `1.3.6.1.4.1.3320.9.181.1.1.7.0`

## 7. Запуск

```bash
cd gpon_turon
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
export PYTHONPATH=src
python run.py
```

По умолчанию:
- host: `0.0.0.0`
- port: `5001`

## 8. Автообновление

Включено в фоне через поток в `app.py`.
Интервал задается env:
- `AUTO_REFRESH_ENABLED=true/false`
- `AUTO_REFRESH_INTERVAL_MINUTES=15`

Есть защита от наложения циклов (`run_refresh_all_once` lock).

## 9. Правила изменений (важно для нового ChatGPT)

1. Сохранять слойную архитектуру (`route/service/repository`).
2. Не ломать единый стиль UI.
3. Не добавлять «магические» значения в роуты; OID и SNMP-логику держать в сервисах.
4. Любое массовое обновление — с защитой от параллельного запуска.
5. Для OFFLINE-логики ONU не использовать `last_seen` как «последний онлайн».
6. При неизвестных SNMP данных возвращать `-`, а не падать 500.
7. Сначала фикс логики, потом косметика.

## 10. Что уже сознательно НЕ делаем

- Мультивендор (сейчас основной vendor: BDCOM)
- Экспорт CSV
- Сложные роли/авторизация
- Тяжелый frontend JS

## 11. Быстрая памятка маршрутов

- `GET /` — главная
- `POST /olts/add` — добавить OLT
- `POST /olts/<id>/delete` — удалить OLT
- `POST /olts/refresh-all` — обновить все OLT
- `GET /olt/<ip>` — страница OLT
- `POST /olt/<ip>/refresh` — обновить OLT
- `GET /olt/<ip>/info` — инфо OLT
- `POST /olt/<ip>/port/<ifindex>/bounce` — перезагрузить порт
- `GET /olt/<ip>/port/<ifindex>` — ONU на порту
- `POST /search` — поиск ONU
- `GET /onu/sn/<sn>` — карточка ONU
- `POST /onu/sn/<sn>/reboot` — перезагрузить ONU
- `GET /onus/new` — последние новые ONU
- `GET /health` — healthcheck

## 12. Статус

Проект в рабочем состоянии. Текущая реализация используется как «чистая база» для дальнейшей доработки.
