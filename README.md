# syschange — Отслеживание изменений в системе

[![Development Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/pooow/syschange)
[![Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> ⚠️ **Проект в стадии alpha-разработки.** Функционал работает, но API может меняться. Не рекомендуется для production без тестирования.

**syschange** — это инструмент для создания снимков состояния Linux-системы и анализа изменений между ними. Полезен для отладки, аудита и понимания того, что происходит с системой после установки пакетов, изменения конфигов или обновлений.

## Зачем это нужно?

Представьте ситуацию: вы установили новый пакет, поменяли пару настроек, перезагрузились... и что-то сломалось. Что именно изменилось? Какие файлы затронуты? Какие процессы появились или исчезли?

**syschange** помогает ответить на эти вопросы:
- 📸 Создаёт снимок системы **до** и **после** изменений
- 🔍 Сравнивает файлы, пакеты, процессы, службы, порты
- 📝 Генерирует понятный отчёт с diff-ами
- 🗂️ Отслеживает изменения текстовых файлов через Git

---

## Быстрый старт

### Установка

1. Клонируйте репозиторий в `/var/tmp`:
   ```bash
   $ cd /var/tmp
   $ git clone https://github.com/pooow/syschange.git
   $ cd syschange
   ```

2. Установите зависимости (системные пакеты для ALT Linux):
   ```bash
   $ su -
   # apt-get update
   # apt-get install python3-module-coloredlogs git-delta jq
   ```

3. Проверьте конфигурацию:
   ```bash
   $ cat config.yaml
   ```

### Первый запуск

**Пример:** Отслеживаем установку пакета `htop`

```bash
# 1. Создаём базовый снимок (до изменений)
# ./syschange.py before install_htop

# 2. Устанавливаем пакет (требуются права root)
# apt-get install htop

# 3. Создаём финальный снимок и отчёт
# ./syschange.py after install_htop

# 4. Смотрим результат (с подсветкой через delta)
# cat /var/log/system_changes/install_htop/full_report.txt | delta
```

**Что вы увидите в отчёте:**
- Новые файлы: `/usr/bin/htop`, `/usr/share/man/man1/htop.1.gz`
- Изменения в `/var/lib/dpkg/status` (установленные пакеты)
- Новые записи в системном логе

---

## Настройка (config.yaml)

Все параметры хранятся в `config.yaml`. **Не хардкодим значения в коде!**

### Основные параметры

```yaml
scan:
  # Где хранятся снимки (по умолчанию: /var/log/system_changes)
  snapshot_base_dir: "/var/log/system_changes"

  # Количество потоков для параллельного хэширования (ускоряет работу)
  max_workers: 16

  # Какие директории сканировать (по умолчанию: вся корневая ФС)
  dirs_to_scan:
    - "/"

excludes:
  # Исключить временные файлы и виртуальные ФС
  - "/tmp"
  - "/proc"
  - "/sys"
  - "/home/*/.cache"  # Кэш браузеров (много мусора)
```

### Изменение параметров

Отредактируйте `config.yaml` под свои нужды. Например, для сканирования только `/etc`:

```yaml
scan:
  dirs_to_scan:
    - "/etc"
```

---

## Примеры использования

### Пример 1: Мониторинг изменений в /etc

**Задача:** Отследить, какие конфиги изменились после настройки веб-сервера.

```bash
# До настройки
# ./syschange.py before webserver_setup

# Настраиваем nginx
# apt-get install nginx
# nano /etc/nginx/sites-available/default
# systemctl restart nginx

# После настройки
# ./syschange.py after webserver_setup

# Отчёт покажет:
# - Новые файлы в /etc/nginx/
# - Изменения в /etc/nginx/sites-available/default
# - Новую службу nginx.service
# - Открытые порты 80 и 443
```

### Пример 2: Отладка после обновления системы

**Задача:** Выяснить, что сломалось после `apt-get upgrade`.

```bash
# До обновления
# ./syschange.py before system_upgrade

# Обновляем систему
# apt-get update && apt-get upgrade -y

# После обновления
# ./syschange.py after system_upgrade

# Отчёт покажет:
# - Обновлённые пакеты
# - Изменённые конфиги (например, /etc/default/grub)
# - Новые/изменённые службы
```

### Пример 3: Мониторинг деплоя приложения

**Задача:** Отследить изменения после установки Java-приложения с PostgreSQL и Keycloak.

```bash
# До деплоя
# ./syschange.py before ca_deploy

# Запускаем установщик
# ./install.sh

# После деплоя
# ./syschange.py after ca_deploy
```

**Реальный пример отчёта** (из `full_report.txt` с подсветкой):

```bash
# cat /var/log/system_changes/ca_deploy/full_report.txt | delta
```

<details>
<summary>📄 Пример вывода full_report.txt (с цветом через delta)</summary>

```diff
System Snapshot Report v2.2.5
Session: ca_deploy
Generated: 2025-12-12 12:49:12

════════════════════════════════════════════════════════════════════════════════
PACKAGES CHANGES
════════════════════════════════════════════════════════════════════════════════
No changes detected.

════════════════════════════════════════════════════════════════════════════════
PROCESSES CHANGES
════════════════════════════════════════════════════════════════════════════════
--- processes_before.txt
+++ processes_after.txt
@@ -137,6 +132,153 @@
+postgres 2921 0.0 0.3 216128 30904 ? Ss 12:40 0:00 /usr/bin/postgres -D /var/lib/pgsql/data -p 5432
+postgres 2922 0.0 0.0  68652  6168 ? Ss 12:40 0:00 postgres: logger
+postgres 2923 0.0 0.5 216584 41432 ? Ss 12:40 0:00 postgres: checkpointer
+causer   3058 0.0 0.0   7032  5340 ? Ss 12:41 0:00 /bin/bash /opt/st-ca/ca-eureka/start_wrapper.sh
+causer   3063 7.0 4.5 4019944 368804 ? Sl 12:41 0:33 java -Xms128m -Xmx256m -jar /opt/st-ca/ca-eureka/ca-eureka-2.0.11.jar
+keycloak 4618 0.0 0.0   6448  4216 ? Ss 12:42 0:00 /bin/sh /opt/keycloak/bin/kc.sh start --optimized
+keycloak 4698 12.7 7.5 3431464 610800 ? Sl 12:42 0:45 java -Xms64m -Xmx512m -jar /opt/keycloak-26.4.2/lib/quarkus-run.jar

════════════════════════════════════════════════════════════════════════════════
SERVICES CHANGES
════════════════════════════════════════════════════════════════════════════════
--- services_before.txt
+++ services_after.txt
@@ -129,6 +131,17 @@
+ca-acme.service          loaded active running   ca-acme
+ca-cep.service           loaded active running   ca-cep
+ca-ces.service           loaded active running   ca-ces
+ca-core.service          loaded active running   ca-core
+ca-eureka.service        loaded active running   ca-eureka
+ca-gateway.service       loaded active running   ca-gateway
+keycloak.service         loaded active running   Keycloak Server
+postgresql.service       loaded active running   PostgreSQL database server

════════════════════════════════════════════════════════════════════════════════
PORTS CHANGES
════════════════════════════════════════════════════════════════════════════════
--- ports_before.txt
+++ ports_after.txt
@@ -5,3 +5,23 @@
+tcp   0.0.0.0:5432   LISTEN   2921/postgres
+tcp   127.0.0.1:8081  LISTEN   4698/java
+tcp   127.0.0.1:8443  LISTEN   4698/java
+tcp   127.0.0.1:8761  LISTEN   3063/java
+tcp   127.0.0.1:9080  LISTEN   5193/java
+tcp   127.0.0.1:9081  LISTEN   5664/java
+tcp   127.0.0.1:9082  LISTEN   5940/java
```

</details>

**JSON-отчёт для автоматизации** (через `jq`):

```bash
# cat /var/log/system_changes/ca_deploy/report.json | jq
```

<details>
<summary>📊 Пример вывода report.json</summary>

```json
{
  "version": "2.2.5",
  "session": "ca_deploy",
  "generated": "2025-12-12T12:49:12.098980",
  "changes": {
    "packages": "",
    "processes": "30+ new processes (PostgreSQL, Keycloak, Java services)",
    "services": "12 new systemd services: postgresql, keycloak, ca-eureka, ca-core, ca-gateway, ca-ui, ca-cep, ca-ces, ca-scep, ca-ocsp, ca-acme, ca-ssh",
    "ports": "20+ new listening ports: 5432 (PostgreSQL), 8081/8443 (Keycloak), 8761 (Eureka), 9080-9087 (CA services)",
    "fs_metadata": "500+ new files in /opt/keycloak, /opt/st-ca, /var/lib/pgsql/data",
    "fs_hashes": "600+ new/changed files"
  }
}
```

</details>

---

## Структура отчёта

После выполнения `syschange.py after <session>` создаются следующие файлы:

```
/var/log/system_changes/<session>/
├── full_report.txt       # Текстовый отчёт с diff-ами (читаемый)
├── report.json           # JSON-отчёт (для парсинга скриптами)
├── fs_before.txt         # Метаданные файлов (до)
├── fs_after.txt          # Метаданные файлов (после)
├── fs_hashes_before.txt  # Хэши файлов (до)
├── fs_hashes_after.txt   # Хэши файлов (после)
├── packages_before.txt   # Установленные пакеты (до)
├── packages_after.txt    # Установленные пакеты (после)
├── processes_before.txt  # Процессы (до)
├── processes_after.txt   # Процессы (после)
├── services_before.txt   # Службы systemd (до)
├── services_after.txt    # Службы systemd (после)
├── ports_before.txt      # Открытые порты (до)
├── ports_after.txt       # Открытые порты (после)
└── fs_git/               # Git-репозиторий с текстовыми файлами
```

**Самое важное:** `full_report.txt` — понятный текстовый отчёт с diff-ами по всем секциям.

---

## Требования

- **Python 3.7+**
- **Git** (для отслеживания изменений текстовых файлов)
- **Права root** (для доступа ко всем файлам системы)

### Рекомендуемые пакеты (ALT Linux)

Для удобной работы установите:

```bash
# apt-get install python3-module-coloredlogs git-delta jq
```

- `python3-module-coloredlogs` — цветной вывод логов
- `git-delta` — красивая подсветка diff-ов
- `jq` — парсинг JSON-отчётов

---

## Дополнительная документация

- [docs/WORKFLOW.md](docs/WORKFLOW.md) — Правила разработки (TDD, Git workflow)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Архитектура проекта (будет добавлено)
- [docs/EXAMPLES.md](docs/EXAMPLES.md) — Расширенные примеры (будет добавлено)

---

## Лицензия

MIT License. Используйте свободно, на свой страх и риск 🙂

---

## Автор

Проект создан для отслеживания изменений в ОС Альт Linux, но работает на любом Linux-дистрибутиве с systemd.

**Полезные ссылки:**
- Репозиторий: https://github.com/pooow/syschange
- Сообщить об ошибке: https://github.com/pooow/syschange/issues
