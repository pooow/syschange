#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
syschange.py — Инструмент для создания снимков системы и анализа изменений.
Версия: 2.3.0
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

# Импорт конфигурации из src/config.py
from src.config import get_config

# ================================
# === ВЕРСИЯ СКРИПТА ===
# ================================

SCRIPT_VERSION = "2.3.0"

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
            errors='replace' if text else None,
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

def is_text_file(path: Path, config: Dict[str, Any]) -> bool:
    """
    Определяет, является ли файл текстовым (строгая проверка).
    Использует несколько эвристик для максимальной точности.
    
    Args:
        path: Путь к файлу
        config: Полная конфигурация из config.yaml
    """
    # Извлекаем параметры из конфигурации
    binary_extensions = config["binary_extensions"]
    max_text_file_size = config["scan"]["max_text_file_size"]
    
    # 1. Исключить по расширению — быстрая проверка для бинарных файлов
    if path.suffix.lower() in binary_extensions:
        log.debug(f"Пропущен (бинарное расширение): {path}")
        return False

    # 2. Явно разрешённые скрытые файлы (часто это конфиги)
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

    # 5. Проверка размера — слишком большие файлы не копируем
    try:
        if path.stat().st_size > max_text_file_size:
            log.debug(f"Пропущен (слишком большой >{max_text_file_size}): {path}")
            return False
    except OSError:
        return False

    # 6. Проверка содержимого: валидный UTF-8 + нет null-байт
    try:
        with path.open('rb') as f:
            data = f.read(512)
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
    
    Args:
        path: Путь для проверки
        excludes: Список исключений из config.yaml
    """
    for ex_path in excludes:
        if '*' in ex_path or '?' in ex_path:
            if fnmatch.fnmatch(path, ex_path):
                return True
        else:
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
    """
    path: Path
    stat_result: os.stat_result
    is_text: bool
    hash_value: Optional[str] = None

def get_file_hash(path: Path) -> Optional[str]:
    """
    Вычисляет SHA256 хэш файла.
    Читает файл блоками по 8192 байт, чтобы не загружать в память большие файлы.
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
    config: Dict[str, Any],
    collect_hashes: bool = True
) -> List[FileInfo]:
    """
    ЕДИНЫЙ ПРОХОД по файловой системе.
    
    Args:
        base_dirs: Список директорий для сканирования
        excludes: Список исключений (из config.yaml + CLI --exclude)
        config: Полная конфигурация из config.yaml
        collect_hashes: Вычислять ли хэши файлов
    
    Returns:
        Список FileInfo объектов с метаданными и хэшами
    """
    # Извлекаем параметры из конфигурации
    min_parallel_size = config["scan"]["min_parallel_size"]
    max_workers = config["scan"]["max_workers"]
    
    results = []
    files_to_hash = []
    
    for scan_dir in base_dirs:
        scan_path = Path(scan_dir)
        if not scan_path.is_dir():
            log.warning(f"Директория не найдена: {scan_dir}")
            continue
        
        log.info(f"Сканирование {scan_dir}...")
        total_hash_time = 0
        
        for root, dirs, files in os.walk(scan_dir, topdown=True):
            dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), excludes)]
            
            for item_name in dirs + files:
                full_path = Path(root) / item_name
                str_path = str(full_path)
                
                if is_excluded(str_path, excludes):
                    continue
                
                try:
                    stat_info = full_path.stat()
                    
                    if full_path.is_file():
                        is_text = is_text_file(full_path, config)
                        file_info = FileInfo(path=full_path, stat_result=stat_info, is_text=is_text)
                        
                        if collect_hashes and is_text:
                            if stat_info.st_size > min_parallel_size:
                                files_to_hash.append(file_info)
                            else:
                                start_time = time.time()
                                file_info.hash_value = get_file_hash(full_path)
                                end_time = time.time()
                                total_hash_time += (end_time - start_time)
                    else:
                        file_info = FileInfo(path=full_path, stat_result=stat_info, is_text=False)
                    
                    results.append(file_info)
                    
                except (FileNotFoundError, PermissionError) as e:
                    log.debug(f"Доступ к {full_path} запрещен: {e}")
                    continue
        
        log.info(f"Время хэширования маленьких файлов: {total_hash_time:.2f} сек")
    
    # ПАРАЛЛЕЛЬНОЕ ХЭШИРОВАНИЕ больших файлов
    if files_to_hash:
        log.info(f"Хэширование {len(files_to_hash)} больших файлов параллельно ({max_workers} потоков)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(get_file_hash, fi.path): fi for fi in files_to_hash}
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
    """
    fs_path = snapshot_dir / f"fs_{suffix}.txt"
    hashes_path = snapshot_dir / f"fs_hashes_{suffix}.txt"
    
    with fs_path.open("w") as fs_file, hashes_path.open("w") as hashes_file:
        for info in file_infos:
            stat_info = info.stat_result
            fs_file.write(
                f"{info.path} {int(stat_info.st_mtime)} {int(stat_info.st_ctime)} "
                f"{get_username(stat_info.st_uid)} {get_groupname(stat_info.st_gid)} "
                f"{format_permissions(stat_info.st_mode)} {stat_info.st_size}\n"
            )
            
            if info.path.is_file() and info.hash_value:
                hashes_file.write(f"{info.path} {info.hash_value}\n")

def copy_text_files_to_git(
    file_infos: List[FileInfo], 
    snapshot_dir: Path, 
    mode: str,
    git_config: Dict[str, Any]
) -> None:
    """
    Копирует текстовые файлы в Git-репозиторий для отслеживания изменений.
    
    Args:
        file_infos: Список информации о файлах
        snapshot_dir: Директория сессии
        mode: Режим (before/after)
        git_config: Конфигурация Git из config.yaml
    """
    if not git_config["enabled"]:
        log.info("Git отслеживание отключено в config.yaml")
        return
    
    if not check_command("git"):
        log.warning("Git не найден. Копирование текстовых файлов пропущено.")
        return

    log.info(f"Отслеживание текстовых файлов в Git ({mode})...")
    git_dir = snapshot_dir / "fs_git"
    git_dir.mkdir(parents=True, exist_ok=True)

    is_initial = not (git_dir / ".git").is_dir()
    if is_initial:
        run_command(["git", "-C", str(git_dir), "init", "-q"])
        run_command(["git", "-C", str(git_dir), "config", "user.email", git_config["user_email"]])
        run_command(["git", "-C", str(git_dir), "config", "user.name", git_config["user_name"]])

    copied_count = 0
    skipped_count = 0
    
    for info in file_infos:
        if not info.is_text:
            skipped_count += 1
            continue
        
        try:
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
    """
    log.info(f"Сбор системных данных ({suffix})...")
    
    state_commands: Dict[str, List[str]] = {
        "processes": ["ps", "aux"],
        "services": ["systemctl", "list-units", "--all"],
        "ports": ["ss", "-tulpn"],
        "passwd": ["getent", "passwd"],
        "group": ["getent", "group"],
    }

    pkg_cmd = get_package_manager_cmd()
    if pkg_cmd:
        state_commands["packages"] = pkg_cmd

    for state, cmd in state_commands.items():
        result = run_command(cmd, check=False)
        (snapshot_dir / f"{state}_{suffix}.txt").write_text(result.stdout)
    
    cron_result = run_command(["crontab", "-l"], check=False)
    cron_content = cron_result.stdout if cron_result.returncode == 0 else "No crontab"
    (snapshot_dir / f"cron_{suffix}.txt").write_text(cron_content)

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

        all_sections = ["packages", "processes", "services", "ports", "passwd", 
                       "group", "cron", "fs", "fs_hashes", "logs", "etc", "fs_diff"]

        for section in all_sections:
            if "all" not in sections and section not in sections:
                continue

            report.write(f"=== {section.upper()} CHANGES ===\n")
            
            diff_content = ""
            
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
            elif section == "logs":
                before_file = snapshot_dir / f"syslog_before.txt"
                after_file = snapshot_dir / f"syslog_after.txt"
                
                if not (before_file.exists() and after_file.exists()):
                    before_file = snapshot_dir / f"messages_before.txt"
                    after_file = snapshot_dir / f"messages_after.txt"
                
                if before_file.exists() and after_file.exists():
                    before_lines = before_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    after_lines = after_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    diff_lines = difflib.unified_diff(before_lines, after_lines, 
                                                     fromfile=before_file.name, tofile=after_file.name)
                    diff_content = "\n".join(diff_lines)
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
    """
    # Загружаем конфигурацию из config.yaml
    config = get_config()
    
    parser = argparse.ArgumentParser(
        description="Инструмент для снимков системы и отслеживания изменений.",
        epilog=f"Пример: ./{Path(__file__).name} before my_test --exclude /usr/share"
    )
    parser.add_argument('-v', '--version', action='version', version=f'%(prog)s {SCRIPT_VERSION}')
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Режим")

    parser_before = subparsers.add_parser("before", help="Базовый снимок.")
    parser_before.add_argument("session_name", help="Имя сессии.")
    parser_before.add_argument("--exclude", action="append", default=[], help="Исключить директорию.")

    parser_after = subparsers.add_parser("after", help="Финальный снимок и отчет.")
    parser_after.add_argument("session_name", help="Имя сессии.")
    parser_after.add_argument("sections", nargs="?", default="all", help="Разделы через запятую ('all' по умолчанию).")
    parser_after.add_argument("--exclude", action="append", default=[], help="Исключить директорию.")

    args = parser.parse_args()

    # Получаем параметры из конфигурации
    snapshot_base_dir = Path(config["scan"]["snapshot_base_dir"])
    session_dir = snapshot_base_dir / args.session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Настраиваем логирование
    log_file = session_dir / "snapshot.log"
    log_level = getattr(logging, config["logging"]["level"].upper(), logging.INFO)
    setup_logging(log_file, log_level)

    log.info(f"Запуск '{args.command}' для сессии '{args.session_name}'")

    # Подготовка общих параметров для сканирования
    scan_params = {
        "base_dirs": config["scan"]["dirs_to_scan"],
        "excludes": config["excludes"] + args.exclude,
        "config": config
    }

    if args.command == "before":
        install_git()
        
        collect_system_state(session_dir, "before")
        
        # ЕДИНЫЙ ПРОХОД по файловой системе
        file_infos = scan_filesystem(**scan_params)
        
        save_filesystem_snapshot(file_infos, session_dir, "before")
        copy_text_files_to_git(file_infos, session_dir, "before", config["git"])
        
        log.info("Базовый снимок создан успешно.")
        log.info(f"Для отчёта запустите: ./{Path(__file__).name} after {args.session_name}")

    elif args.command == "after":
        if not (session_dir / "fs_before.txt").exists():
            log.error("Базовый снимок не найден! Сначала запустите 'before'.")
            sys.exit(1)
        
        collect_system_state(session_dir, "after")
        
        log.info("Выполняется финальное сканирование...")
        file_infos = scan_filesystem(**scan_params)
        
        save_filesystem_snapshot(file_infos, session_dir, "after")
        copy_text_files_to_git(file_infos, session_dir, "after", config["git"])
        
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

