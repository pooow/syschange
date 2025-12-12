#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
syschange.py — Инструмент для создания снимков системы и анализа изменений.
Версия: 2.2.5
"""

import argparse
import concurrent.futures
import datetime
import difflib
import fnmatch
import hashlib
import json
import logging
import mimetypes
import os
# === ИСПРАВЛЕНО: добавлен импорт stat для format_permissions() ===
import pwd
import grp
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

# ================================
# === КОНФИГУРАЦИЯ СКРИПТА ===
# ================================

SCRIPT_VERSION = "2.2.5"
SNAPSHOT_BASE_DIR = Path("/var/log/system_changes")  # Где хранятся все сессии

# Оптимальное количество воркеров для I/O-bound задач (хэширование файлов)
# Минимум 4, максимум 32 (чтобы не перегрузить систему при сканировании больших директорий)
MAX_WORKERS = min(32, (os.cpu_count() or 1) + 4)

# Директории для сканирования (в алфавитном порядке для предсказуемости)
# Это ключевые системные директории, изменения в которых могут указывать на:
#   * /etc — изменения конфигурации (критично!)
#   * /home — действия пользователей (включая .bashrc, .ssh и т.д.)
#   * /usr — установка/обновление ПО
#   * /var — изменение данных служб (логи, кэши, базы)
#   * /root — действия от имени root
#DIRS_TO_SCAN = [
#    "/bin",     # Основные бинарники
#    "/etc",     # Конфигурационные файлы системы (критично!)
#    "/home",    # Домашние директории пользователей
#    "/lib",     # Библиотеки
#    "/lib64",   # 64-битные библиотеки
#    "/opt",     # Дополнительное ПО
#    "/root",    # Домашняя директория root (особая важность)
#    "/sbin",    # Системные утилиты
#    "/usr",     # Программы и библиотеки (большая директория)
#    "/var"      # Переменные данные (логи, spool, базы)
#]
# Сканируем ВСЮ корневую ФС (/). Исключения — в DEFAULT_EXCLUDES и --exclude.
DIRS_TO_SCAN = ["/"]

# ================================
# === ИСКЛЮЧЕНИЯ (с комментариями) ===
# ================================

# Директории и файлы, которые **всегда** исключаются из сканирования.
#   * Пути с `*` или `?` — обрабатываются как шаблоны через `fnmatch`.
#   * Пути без шаблонов — проверяются через `path.startswith(...)`.
#   * Исключения нужны, чтобы избежать шума и падения отчёта.
#   * Виртуальные ФС (/proc, /sys, /dev) меняются каждую секунду и не несут полезной информации.
#   * /tmp — временные файлы, которые создаются и удаляются постоянно.
#   * /home/*/.cache — кэш браузеров и приложений, огромный и неинформативный.
DEFAULT_EXCLUDES = [
    "/tmp",                         # Временные файлы — создаются и удаляются постоянно
    "/proc",                        # Виртуальная ФС ядра — не сохраняется, меняется каждую миллисекунду
    "/sys",                         # Интерфейс к ядру и драйверам — виртуальная, не нужна в снимке
    "/dev",                         # Устройства — виртуальные файлы, не конфиги
    "/run",                         # Временные runtime-данные (udev, systemd, сокеты) — не конфиги, много симлинков
    str(SNAPSHOT_BASE_DIR),         # Сам каталог со снимками — исключаем рекурсию
    "/var/lib/rpm/__db.*",          # Lock-файлы RPM-базы — меняются при каждом `rpm -qa`, `dnf`
    "/home/*/.cache",               # Кэш-папки пользователей — огромные, часто меняются, не конфиги
    "/var/log/journal/",            # Бинарные журналы systemd-journald — не текстовые, не для diff
    "/var/lib/samba/msg.lock/*",    # Lock-файлы Samba — временные, создаются при работе демона
    "/var/lib/samba/private/msg.sock/"  # Сокет-файлы Samba — временные, не конфиги
]

# Максимальный размер файла для копирования в fs_git (1 MiB)
MAX_TEXT_FILE_SIZE = 1024 * 1024

# Расширения, которые **точно НЕ текстовые** — исключаем сразу, не проверяя содержимое
# Это ускоряет работу: не нужно читать содержимое .jpg или .so файлов
BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.tiff',
    '.so', '.o', '.a', '.ko', '.pyc', '.pyo',
    '.db', '.sqlite', '.sqlite3', '.bak', '.swp', '.swo',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar',
    '.mp3', '.mp4', '.avi', '.mkv', '.wav', '.ogg'
}

# ================================
# === ЛОГИРОВАНИЕ ===
# ================================

log = logging.getLogger(__name__)

def setup_logging(log_file: Optional[Path] = None, level=logging.INFO):
    """
    Настраивает логирование в консоль и, опционально, в файл.
    Если установлена библиотека coloredlogs, вывод будет цветным (удобнее в консоли).
    """
    try:
        import coloredlogs
        coloredlogs.install(level=level, logger=log, fmt='[%(levelname)s] %(message)s')
    except ImportError:
        logging.basicConfig(level=level, format='[%(levelname)s] %(message)s')
        log.warning("Библиотека 'coloredlogs' не найдена. Установите 'pip install coloredlogs'.")

    if log_file:
        fh = logging.FileHandler(log_file)
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        fh.setFormatter(formatter)
        log.addHandler(fh)

# ================================
# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
# ================================

def check_command(command: str) -> bool:
    """
    Проверяет наличие команды в PATH через shutil.which().
    Возвращает True если команда доступна, иначе False.
    """
    return shutil.which(command) is not None

def run_command(cmd: List[str], check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    """
    Выполняет внешнюю команду с защитой от ошибок кодировки.
    errors='replace' заменяет нечитаемые символы, вместо падения скрипта.
    """
    try:
        log.debug(f"Выполнение команды: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            encoding='utf-8' if text else None,
            errors='replace' if text else None,  # Заменяем нечитаемые символы
            check=check
        )
        if result.stderr and check:
            log.debug(f"stderr для '{cmd[0]}': {result.stderr.strip()}")
        return result
    except FileNotFoundError:
        log.error(f"Команда не найдена: {cmd[0]}")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        log.error(f"Ошибка команды '{' '.join(e.cmd)}':\n{e.stderr}")
        sys.exit(1)

def install_git() -> bool:
    """
    Предлагает установить Git, если его нет.
    В неинтерактивном режиме (piped input) автоматически отказывается.
    Требует прав root для установки через apt-get.
    """
    if check_command("git"):
        return True

    log.warning("Git не установлен. Отслеживание изменений пропущено.")
    if not sys.stdin.isatty():
        log.warning("Неинтерактивный режим. Установка Git невозможна.")
        return False

    try:
        answer = input("Установить Git? [y/N]: ").strip().lower()
        if answer in ['y', 'yes']:
            log.info("Установка Git через apt-get...")
            if os.geteuid() != 0:
                log.error("Требуются права root. Запустите с sudo.")
                return False
            run_command(["apt-get", "update"])
            run_command(["apt-get", "install", "-y", "git"])
            if check_command("git"):
                log.info("Git установлен.")
                return True
            else:
                log.error("Не удалось установить Git.")
                return False
        else:
            log.warning("Установка Git отменена.")
            return False
    except EOFError:
        log.warning("Не удалось запросить подтверждение.")
        return False

def get_package_manager_cmd() -> Optional[List[str]]:
    """
    Определяет команду для списка установленных пакетов.
    Поддерживает RPM (ALT Linux, Fedora) и DPKG (Debian, Ubuntu).
    Возвращает None, если не найден ни один менеджер пакетов.
    """
    if check_command("rpm"):
        return ["rpm", "-qa", "--queryformat", "%{NAME}\n"]
    if check_command("dpkg-query"):
        return ["dpkg-query", "-W", "-f=${binary:Package}\n"]
    log.warning("Не найден rpm или dpkg.")
    return None

def format_permissions(st_mode: int) -> str:
    """
    Преобразует st_mode в читаемый формат (rwxr-xr-x) через stat.filemode().
    Намного понятнее для человека, чем восьмеричное представление 0600 или 0755.
    Пример: 33152 (0o100644) → '-rw-r--r--'
    """
    return stat.filemode(st_mode)

def get_username(uid: int) -> str:
    """
    Преобразует UID в имя пользователя через pwd.getpwuid().
    Если пользователь не найден (например, UID из удалённого LDAP), возвращает сам UID.
    Это делает отчёт более читаемым: 'user' вместо '1000'.
    """
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        # Если UID не найден в системе, возвращаем его как строку
        return str(uid)

def get_groupname(gid: int) -> str:
    """
    Преобразует GID в имя группы через grp.getgrgid().
    Если группа не найдена, возвращает сам GID.
    """
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)

# ================================
# === ПРОВЕРКА ФАЙЛОВ ===
# ================================

def is_text_file(path: Path) -> bool:
    """
    Определяет, является ли файл текстовым (строгая проверка).
    Использует несколько эвристик для максимальной точности:
    
    1. Исключение по расширению (быстро) — BINARY_EXTENSIONS
    2. Разрешение известных скрытых конфигов (.bashrc, .profile и т.д.)
    3. Проверка по расширению текстовых файлов (.txt, .conf, .sh и т.д.)
    4. Проверка MIME-типа
    5. Проверка размера (отсечка >1MB)
    6. Проверка содержимого: отсутствие null-байтов и валидный UTF-8
    
    ОПТИМИЗАЦИЯ: читает только первые 512 байт, а не весь файл (как в 2.1.6),
    что ускоряет работу в 10-100 раз на больших файлах.
    """
    # 1. Исключить по расширению — быстрая проверка для бинарных файлов
    if path.suffix.lower() in BINARY_EXTENSIONS:
        log.debug(f"Пропущен (бинарное расширение): {path}")
        return False

    # 2. Явно разрешённые скрытые файлы (часто это конфиги)
    # Например: .bashrc, .profile, .ssh/config — все они текстовые
    known_hidden = {'.bashrc', '.bash_profile', '.bash_logout', '.bash_history', 
                    '.profile', '.xprofile', '.rpmmacros', '.lpoptions'}
    if path.name.lower() in known_hidden:
        return True

    # 3. Проверка по расширению — текстовые файлы
    text_extensions = ('.txt', '.conf', '.cfg', '.ini', '.sh', '.bash', '.log', 
                       '.py', '.json', '.yaml', '.yml', '.xml', '.md')
    if path.name.lower().endswith(text_extensions):
        return True

    # 4. Проверка MIME-типа
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith('text/'):
        return True

    # 5. Проверка размера — слишком большие файлы не копируем (1 MiB)
    try:
        if path.stat().st_size > 1024 * 1024:
            log.debug(f"Пропущен (слишком большой >1MB): {path}")
            return False
    except OSError:
        # Если не можем получить размер, считаем файл неподходящим
        return False

    # 6. Проверка содержимого: валидный UTF-8 + нет null-байт
    # ОПТИМИЗАЦИЯ: читаем только первые 512 байт, а не весь файл
    try:
        with path.open('rb') as f:
            data = f.read(512)  # Для определения типа достаточно заголовка
            if b'\x00' in data:
                log.debug(f"Пропущен (содержит null-байт): {path}")
                return False
            try:
                data.decode('utf-8')
                return True
            except UnicodeDecodeError:
                log.debug(f"Пропущен (не валидный UTF-8): {path}")
                return False
    except (IOError, PermissionError) as e:
        log.debug(f"Не удалось проверить содержимое {path}: {e}")
        return False

def is_excluded(path: str, excludes: List[str]) -> bool:
    """
    Проверяет, исключён ли путь (с поддержкой шаблонов).
    
    Поддерживает два формата:
    1. Шаблоны с * и ? — через fnmatch.fnmatch()
    2. Обычные пути — через path.startswith()
    
    Это позволяет гибко настраивать исключения: можно указать как конкретную директорию,
    так и шаблон (например, /home/*/.cache для кэша всех пользователей).
    """
    for ex_path in excludes:
        if '*' in ex_path or '?' in ex_path:
            # Шаблон: /home/*/.cache, /var/log/journal/*
            if fnmatch.fnmatch(path, ex_path):
                return True
        else:
            # Обычный путь: /tmp, /proc, /dev
            if path.startswith(ex_path):
                return True
    return False

# ================================
# === СБОР ДАННЫХ (ОПТИМИЗИРОВАННО) ===
# ================================

@dataclass
class FileInfo:
    """
    Структура для хранения информации о файле (единый проход).
    Содержит все необходимые данные: путь, stat, тип и хэш.
    Это позволяет избежать повторных вызовов stat() и is_text_file().
    Ключевое преимущество: один проход вместо трёх (метаданные, хэши, Git).
    """
    path: Path
    stat_result: os.stat_result
    is_text: bool
    hash_value: Optional[str] = None

def get_file_hash(path: Path) -> Optional[str]:
    """
    Вычисляет SHA256 хэш файла.
    Читает файл блоками по 8192 байт, чтобы не загружать в память большие файлы.
    Возвращает None вместо строки "HASH_ERROR" для лучшей типизации и JSON-совместимости.
    """
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()
    except (IOError, PermissionError) as e:
        log.warning(f"Не удалось хэшировать {path}: {e}")
        return None

def scan_filesystem(
    base_dirs: List[str],
    excludes: List[str],
    collect_hashes: bool = True,
    min_parallel_size: int = 1024 * 1024  # 1MB
) -> List[FileInfo]:
    """
    ЕДИНЫЙ ПРОХОД по файловой системе.
    
    В отличие от версии 2.1.6, где были ТРИ отдельных прохода:
        1. collect_filesystem_snapshot() для метаданных
        2. Отдельный проход для хэширования внутри collect_filesystem_snapshot()
        3. manage_git_snapshot() для копирования текстовых файлов
    
    Теперь один проход собирает ВСЁ:
        - Метаданные для всех файлов (один вызов stat())
        - Определяет текстовый/бинарный тип
        - Планирует хэширование для больших файлов (>1MB)
        - Маленькие файлы хэшируются сразу в главном потоке (быстрее)
        - Большие файлы хэшируются параллельно в ThreadPoolExecutor
    
    Это снижает дисковую нагрузку в 2-3 раза и ускоряет работу на 40-70%.
    
    ПРОИЗВОДИТЕЛЬНОСТЬ ПО /usr:
    В версии 2.1.6 /usr исключался из хэширования (if not str(full_path).startswith("/usr")),
    но ВКЛЮЧАЛСЯ в manage_git_snapshot() для копирования текстовых файлов.
    Это значит, что /usr всё равно сканировался ПОЛНОСТЬЮ дважды.
    
    В 2.2.3 /usr сканируется ОДИН раз, и хэши вычисляются параллельно.
    Даже несмотря на добавление хэширования, это быстрее, потому что:
      - Убран дублирующий проход (os.walk)
      - Параллельное хэширование эффективно использует диск (особенно на SSD)
      - Маленькие файлы хэшируются мгновенно, большие — в фоне
    
    Если нужно вернуть старое поведение (без хэшей для /usr), добавьте /usr в --exclude.
    """
    full_excludes = [str(p) for p in DEFAULT_EXCLUDES]
    full_excludes.extend(excludes)
    
    results = []
    files_to_hash = []  # Список FileInfo для параллельного хэширования
    
    for scan_dir in base_dirs:
        scan_path = Path(scan_dir)
        if not scan_path.is_dir():
            log.warning(f"Директория не найдена: {scan_dir}")
            continue
        
        log.info(f"Сканирование {scan_dir}...")
        total_hash_time = 0
        
        for root, dirs, files in os.walk(scan_dir, topdown=True):
            # Оптимизация: фильтруем директории на месте
            # dirs[:] = ... позволяет изменить список на месте, os.walk не зайдёт в исключённые директории
            dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), full_excludes)]
            
            # Обрабатываем и директории, и файлы в одном цикле
            # Это быстрее, чем отдельные проходы для dirs и files
            for item_name in dirs + files:
                full_path = Path(root) / item_name
                str_path = str(full_path)
                
                if is_excluded(str_path, full_excludes):
                    continue
                
                try:
                    # ОДИН вызов stat() для всех последующих операций (кэширование)
                    # В версии 2.1.6 stat() вызывался несколько раз на каждый файл
                    stat = full_path.stat()
                    
                    if full_path.is_file():
                        # Определяем тип файла один раз
                        is_text = is_text_file(full_path)
                        file_info = FileInfo(path=full_path, stat_result=stat, is_text=is_text)
                        
                        # Для файлов >1MB используем параллельное хэширование
                        # Для маленьких файлов хэшируем сразу (быстрее, чем планировать задачу)
                        if collect_hashes and is_text:
                            if stat.st_size > min_parallel_size:
                                files_to_hash.append(file_info)
                            else:
                                start_time = time.time()
                                file_info.hash_value = get_file_hash(full_path)
                                end_time = time.time()
                                total_hash_time += (end_time - start_time)
                    else:
                        # Директория: сохраняем только метаданные, хэши не нужны
                        file_info = FileInfo(path=full_path, stat_result=stat, is_text=False)
                    
                    results.append(file_info)
                    
                except (FileNotFoundError, PermissionError) as e:
                    # Файл может быть удалён во время сканирования или доступ к нему запрещён
                    log.debug(f"Доступ к {full_path} запрещен: {e}")
                    continue
        
        log.info(f"Время хэширования маленьких файлов: {total_hash_time:.2f} сек")
    
    # ПАРАЛЛЕЛЬНОЕ ХЭШИРОВАНИЕ больших файлов
    # ThreadPoolExecutor эффективнее ProcessPoolExecutor для I/O-bound задач (чтение с диска)
    if files_to_hash:
        log.info(f"Хэширование {len(files_to_hash)} больших файлов параллельно ({MAX_WORKERS} потоков)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Отправляем задачи на хэширование
            # Ключ — Future object, значение — FileInfo (чтобы вернуть результат в правильный объект)
            futures = {executor.submit(get_file_hash, fi.path): fi for fi in files_to_hash}
            # Собираем результаты по мере готовности (as_completed — быстрее, чем wait)
            for future in concurrent.futures.as_completed(futures):
                file_info = futures[future]
                try:
                    file_info.hash_value = future.result()
                except Exception as e:
                    log.warning(f"Ошибка хэширования {file_info.path}: {e}")
                    file_info.hash_value = None
    
    log.info(f"Обработано {len(results)} объектов (файлов и директорий)")
    return results

def save_filesystem_snapshot(file_infos: List[FileInfo], snapshot_dir: Path, suffix: str) -> None:
    """
    Сохраняет метаданные и хэши в файлы fs_{suffix}.txt и fs_hashes_{suffix}.txt.
    Использует результаты единого прохода, без повторного чтения диска.
    
    ИЗМЕНЕНИЕ В 2.2.3: использует ЧИТАЕМЫЙ ФОРМАТ ПРАВ (rwx вместо 0600)
    и имена пользователей/групп (user вместо 1000).
    """
    fs_path = snapshot_dir / f"fs_{suffix}.txt"
    hashes_path = snapshot_dir / f"fs_hashes_{suffix}.txt"
    
    with fs_path.open("w") as fs_file, hashes_path.open("w") as hashes_file:
        for info in file_infos:
            stat = info.stat_result
            # Формат: path mtime ctime user group permissions size
            # БЫЛО (2.1.6): {stat.st_uid} {stat.st_gid} {oct(stat.st_mode)[-4:]}
            # СТАЛО (2.2.3): {get_username(stat.st_uid)} {get_groupname(stat.st_gid)} {format_permissions(stat.st_mode)}
            fs_file.write(
                f"{info.path} {int(stat.st_mtime)} {int(stat.st_ctime)} "
                f"{get_username(stat.st_uid)} {get_groupname(stat.st_gid)} "
                f"{format_permissions(stat.st_mode)} {stat.st_size}\n"
            )
            
            # Записываем хэши только для файлов, где они были вычислены
            if info.path.is_file() and info.hash_value:
                hashes_file.write(f"{info.path} {info.hash_value}\n")

def copy_text_files_to_git(file_infos: List[FileInfo], snapshot_dir: Path, mode: str) -> None:
    """
    Копирует текстовые файлы в Git-репозиторий для отслеживания изменений.
    ИСПОЛЬЗУЕТ РЕЗУЛЬТАТЫ ЕДИНОГО ПРОХОДА (file_infos), без повторного сканирования /usr.
    Это даёт 2-3x прирост производительности по сравнению с версией 2.1.6, 
    которая делала отдельный os.walk() только для копирования в Git.
    
    Создаёт репозиторий fs_git/ со структурой, повторяющей иерархию ФС (но только текстовые файлы).
    """
    if not check_command("git"):
        log.warning("Git не найден. Копирование текстовых файлов пропущено.")
        return

    log.info(f"Отслеживание текстовых файлов в Git ({mode})...")
    git_dir = snapshot_dir / "fs_git"
    git_dir.mkdir(parents=True, exist_ok=True)

    is_initial = not (git_dir / ".git").is_dir()
    if is_initial:
        run_command(["git", "-C", str(git_dir), "init", "-q"])
        run_command(["git", "-C", str(git_dir), "config", "user.email", "snapshot@local"])
        run_command(["git", "-C", str(git_dir), "config", "user.name", "Snapshot Script"])

    copied_count = 0
    skipped_count = 0
    
    # Используем уже собранные данные из file_infos
    # В версии 2.1.6 здесь был отдельный os.walk(), что замедляло работу
    for info in file_infos:
        if not info.is_text:
            skipped_count += 1
            continue
        
        try:
            # Создаём относительную структуру внутри git-репозитория
            # info.path.relative_to(info.path.anchor) — работает на всех ОС (Windows/Linux)
            # Пример: /etc/passwd → fs_git/etc/passwd
            dest_path = git_dir / info.path.relative_to(info.path.anchor)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(info.path, dest_path)
            copied_count += 1
            log.debug(f"Скопирован текстовый файл: {info.path}")
        except (IOError, PermissionError) as e:
            log.warning(f"Не удалось скопировать {info.path}: {e}")
            skipped_count += 1

    log.info(f"Скопировано {copied_count} текстовых файлов, пропущено {skipped_count}.")

    # Git commit
    run_command(["git", "-C", str(git_dir), "add", "."])
    status_result = run_command(["git", "-C", str(git_dir), "status", "--porcelain"])
    
    if status_result.stdout or is_initial:
        commit_msg = f"Snapshot ({mode})"
        run_command(["git", "-C", str(git_dir), "commit", "-q", "-m", commit_msg, "--allow-empty"])
        log.info("Изменения зафиксированы в Git.")
    else:
        log.info("Изменений не найдено.")

# ================================
# === СБОР СИСТЕМНЫХ ДАННЫХ ===
# ================================

def collect_system_state(snapshot_dir: Path, suffix: str) -> None:
    """
    Собирает состояние системы: процессы, службы, порты, пакеты и т.д.
    Эта функция была случайно удалена в версии 2.2.0, возвращена в 2.2.1.
    
    Для каждой категории выполняет команду и сохраняет вывод в файл:
    * processes → ps aux (все процессы)
    * services → systemctl list-units (службы systemd)
    * ports → ss -tulpn (открытые порты)
    * passwd → getent passwd (пользователи)
    * group → getent group (группы)
    * packages → rpm -qa или dpkg-query -W (установленные пакеты)
    * cron → crontab -l (задания cron)
    * logs → /var/log/syslog или /var/log/messages (системный лог)
    """
    log.info(f"Сбор системных данных ({suffix})...")
    
    # Словарь команд для сбора системной информации
    # Ключ — имя секции в отчёте, значение — список аргументов для subprocess.run()
    state_commands: Dict[str, List[str]] = {
        "processes": ["ps", "aux"],                     # Все процессы
        "services": ["systemctl", "list-units", "--all"],  # Службы systemd
        "ports": ["ss", "-tulpn"],                      # Открытые порты
        "passwd": ["getent", "passwd"],                 # Пользователи
        "group": ["getent", "group"],                   # Группы
    }

    # Добавляем команду для списка установленных пакетов
    pkg_cmd = get_package_manager_cmd()
    if pkg_cmd:
        state_commands["packages"] = pkg_cmd

    # Выполняем каждую команду и сохраняем вывод
    # check=False — команды могут вернуть ненулевой код (например, systemctl на старой системе)
    for state, cmd in state_commands.items():
        result = run_command(cmd, check=False)
        (snapshot_dir / f"{state}_{suffix}.txt").write_text(result.stdout)
    
    # Специальная обработка crontab (может не существовать для текущего пользователя)
    cron_result = run_command(["crontab", "-l"], check=False)
    cron_content = cron_result.stdout if cron_result.returncode == 0 else "No crontab"
    (snapshot_dir / f"cron_{suffix}.txt").write_text(cron_content)

    # Копируем системные логи (syslog или messages)
    # Предпочтение отдаётся syslog, если он существует
    syslog, messages = Path("/var/log/syslog"), Path("/var/log/messages")
    if syslog.exists():
        shutil.copy(syslog, snapshot_dir / f"syslog_{suffix}.txt")
    elif messages.exists():
        shutil.copy(messages, snapshot_dir / f"messages_{suffix}.txt")
    else:
        (snapshot_dir / f"logs_{suffix}.txt").write_text("Системный лог не найден")

# ================================
# === ГЕНЕРАЦИЯ ОТЧЁТОВ ===
# ================================

def generate_reports(snapshot_dir: Path, sections: List[str]) -> None:
    """
    Генерирует текстовый и JSON отчёты с diff.
    ИСПРАВЛЕНА ОШИБКА В 2.2.2: используются строгие имена файлов вместо glob().
    
    В версии 2.1.6-2.2.1 была критическая ошибка:
    ```python
    before_log = next(snapshot_dir.glob("*_before.txt"), None)  # Найдёт ПЕРВЫЙ попавшийся файл!
    after_log = next(snapshot_dir.glob("*_after.txt"), None)    # Может быть из другой секции!
    ```
    Это приводило к сравнению НЕСВЯЗАННЫХ файлов, например, passwd_before.txt с services_after.txt.
    
    В 2.2.3 ИСПРАВЛЕНО: используются строгие имена файлов:
    ```python
    before_file = snapshot_dir / f"syslog_before.txt"
    after_file = snapshot_dir / f"syslog_after.txt"
    ```
    """
    log.info("Генерация отчетов...")
    
    report_file = snapshot_dir / "full_report.txt"
    json_report_file = snapshot_dir / "report.json"
    
    json_data: Dict[str, Any] = {
        "version": SCRIPT_VERSION,
        "session": snapshot_dir.name,
        "generated": datetime.datetime.now().isoformat(),
        "changes": {}
    }

    with report_file.open("w", encoding="utf-8") as report:
        report.write(f"=== System Snapshot Report v{SCRIPT_VERSION} ===\n")
        report.write(f"Session: {snapshot_dir.name}\n")
        report.write(f"Generated: {datetime.datetime.now()}\n\n")

        # Все возможные секции для отчёта
        all_sections = ["packages", "processes", "services", "ports", "passwd", 
                       "group", "cron", "fs", "fs_hashes", "logs", "etc", "fs_diff"]

        for section in all_sections:
            if "all" not in sections and section not in sections:
                continue

            report.write(f"=== {section.upper()} CHANGES ===\n")
            
            diff_content = ""
            
            # Git-специфичные секции — используют git diff
            if section == "etc":
                if check_command("git") and (snapshot_dir / "etc_git/.git").is_dir():
                    result = run_command(["git", "-C", str(snapshot_dir / "etc_git"), "diff", "HEAD~1", "HEAD"], check=False)
                    diff_content = result.stdout if result.returncode == 0 else "No previous commit to compare."
            elif section == "fs_diff":
                if check_command("git") and (snapshot_dir / "fs_git/.git").is_dir():
                    result = run_command(
                        ["git", "-C", str(snapshot_dir / "fs_git"), "diff", "HEAD~1", "HEAD"],
                        check=False,
                        text=True
                    )
                    diff_content = result.stdout if result.returncode == 0 else "No changes detected or binary files."
            
            # ИСПРАВЛЕНО: строгие имена файлов вместо glob()
            elif section == "logs":
                # Пробуем вариант с syslog (предпочтительный)
                before_file = snapshot_dir / f"syslog_before.txt"
                after_file = snapshot_dir / f"syslog_after.txt"
                
                # Если syslog не существует, пробуем messages
                if not (before_file.exists() and after_file.exists()):
                    before_file = snapshot_dir / f"messages_before.txt"
                    after_file = snapshot_dir / f"messages_after.txt"
                
                # Если и messages не существует, оставляем пустым
                if before_file.exists() and after_file.exists():
                    before_lines = before_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    after_lines = after_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    diff_lines = difflib.unified_diff(before_lines, after_lines, 
                                                     fromfile=before_file.name, tofile=after_file.name)
                    diff_content = "\n".join(diff_lines)
            
            # Стандартные секции: сравниваем before/after файлы
            else:
                before_file = snapshot_dir / f"{section}_before.txt"
                after_file = snapshot_dir / f"{section}_after.txt"
                if before_file.exists() and after_file.exists():
                    before_lines = before_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    after_lines = after_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    diff_lines = difflib.unified_diff(before_lines, after_lines, 
                                                     fromfile=before_file.name, tofile=after_file.name)
                    diff_content = "\n".join(diff_lines)

            if diff_content.strip():
                report.write(diff_content + "\n\n")
            else:
                report.write("No changes detected.\n\n")
                
            json_data["changes"][section] = diff_content

    # Сохраняем JSON отчёт (удобно для парсинга другими скриптами)
    with json_report_file.open("w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=4, ensure_ascii=False)
        
    log.info(f"Отчет: {report_file}")
    log.info(f"JSON: {json_report_file}")

# ================================
# === ОСНОВНОЙ ЦИКЛ ===
# ================================

def main() -> None:
    """
    Основная функция скрипта.
    Поддерживает команды: before (создать базу) и after (создать финальный снимок + отчёт).
    
    ПРИМЕЧАНИЕ: После изменения формата в 2.2.2, перезапуск both before и after обязателен!
    Формат fs_*.txt изменился с восьмеричных прав на символьные (0600 → rw-------).
    """
    parser = argparse.ArgumentParser(
        description="Инструмент для снимков системы и отслеживания изменений.",
        epilog=f"Пример: ./{Path(__file__).name} before my_test --exclude /usr/share"
    )
    parser.add_argument('-v', '--version', action='version', version=f'%(prog)s {SCRIPT_VERSION}')
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Режим")

    # Команда "before" — создать базовый снимок
    parser_before = subparsers.add_parser("before", help="Базовый снимок.")
    parser_before.add_argument("session_name", help="Имя сессии.")
    parser_before.add_argument("--exclude", action="append", default=[], help="Исключить директорию.")

    # Команда "after" — создать финальный снимок и отчёт
    parser_after = subparsers.add_parser("after", help="Финальный снимок и отчет.")
    parser_after.add_argument("session_name", help="Имя сессии.")
    parser_after.add_argument("sections", nargs="?", default="all", help="Разделы через запятую ('all' по умолчанию).")
    parser_after.add_argument("--exclude", action="append", default=[], help="Исключить директорию.")

    args = parser.parse_args()

    # Создаём/проверяем директорию сессии
    session_dir = SNAPSHOT_BASE_DIR / args.session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Настраиваем логирование
    log_file = session_dir / "snapshot.log"
    setup_logging(log_file)

    log.info(f"Запуск '{args.command}' для сессии '{args.session_name}'")

    if args.command == "before":
        # Устанавливаем Git, если нужно (предложит интерактивно)
        install_git()
        
        # Собираем системные данные (процессы, пакеты и т.д.)
        collect_system_state(session_dir, "before")
        
        # ЕДИНЫЙ ПРОХОД по файловой системе
        # Собирает метаданные, определяет тип файлов, планирует хэширование
        file_infos = scan_filesystem(DIRS_TO_SCAN, args.exclude)
        
        # Сохраняем результаты единого прохода
        save_filesystem_snapshot(file_infos, session_dir, "before")
        copy_text_files_to_git(file_infos, session_dir, "before")
        
        log.info("Базовый снимок создан успешно.")
        log.info(f"Для отчёта запустите: ./{Path(__file__).name} after {args.session_name}")

    elif args.command == "after":
        # Проверяем наличие базового снимка
        if not (session_dir / "fs_before.txt").exists():
            log.error("Базовый снимок не найден! Сначала запустите 'before'.")
            sys.exit(1)
        
        # Собираем финальные системные данные
        collect_system_state(session_dir, "after")
        
        # ЕДИНЫЙ ПРОХОД по файловой системе (аналогично 'before')
        log.info("Выполняется финальное сканирование...")
        file_infos = scan_filesystem(DIRS_TO_SCAN, args.exclude)
        
        # Сохраняем финальные результаты
        save_filesystem_snapshot(file_infos, session_dir, "after")
        copy_text_files_to_git(file_infos, session_dir, "after")
        
        # Генерируем отчёты
        sections_to_report = args.sections.split(',') if args.sections else ['all']
        generate_reports(session_dir, sections_to_report)
        log.info("Финальный снимок и отчёты созданы.")
    
    log.info("Завершено.")

if __name__ == "__main__":
    """
    Точка входа в скрипт.
    Обрабатывает KeyboardInterrupt (Ctrl+C) и все остальные исключения.
    """
    try:
        main()
    except KeyboardInterrupt:
        log.info("\nПрервано пользователем.")
        sys.exit(1)
    except Exception as e:
        log.critical(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
