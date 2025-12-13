#!/usr/bin/env python3
"""
Тест для проверки отсутствия хардкода настроек в проекте syschange.

Проверяет:
1. Отсутствие дефолтных значений во ВСЕХ Python-модулях проекта
2. Наличие всех обязательных параметров в config.yaml
3. Корректное чтение параметров через src/config.py

Принцип: Единственный источник правды (Single Source of Truth) - это config.yaml.
Легальных дефолтов в коде быть не должно.

Автор: pooow (с помощью AI)
Дата: Декабрь 2025
"""
import pytest
import os
import ast
from pathlib import Path


# Список конфигурационных ключей, для которых запрещены дефолты в коде
# Все эти параметры должны читаться только из config.yaml!
FORBIDDEN_CONFIG_KEYS_WITH_DEFAULTS = [
    # Логирование
    "level",
    "use_colors",
    
    # Сканирование
    "snapshot_base_dir",
    "max_workers",
    "dirs_to_scan",
    "max_text_file_size",
    "min_parallel_size",
    
    # Git
    "enabled",
    "user_email",
    "user_name",
    
    # Исключения и расширения (списки)
    "excludes",
    "binary_extensions",
]


class TestConfigNoHardcode:
    """
    Тесты для проверки правила "Хардкод запрещен" из docs/WORKFLOW.md
    """

    def test_snapshot_base_dir_from_config(self):
        """
        Проверяем, что snapshot_base_dir читается из config.yaml.
        
        ЗАЧЕМ: В syschange.py есть константа SNAPSHOT_BASE_DIR = Path("/var/log/system_changes")
        ЧТО ПРОВЕРЯЕМ: src/config.py должен возвращать значение из config.yaml
        КАК РАБОТАЕТ: Используем src/config.py для получения параметра
        """
        from src.config import get_config
        
        config = get_config()
        snapshot_dir = config["scan"]["snapshot_base_dir"]
        
        assert snapshot_dir == "/var/log/system_changes", \
            "snapshot_base_dir должен читаться из config.yaml"

    def test_max_workers_from_config(self):
        """
        Проверяем, что max_workers читается из config.yaml.
        
        ЗАЧЕМ: В syschange.py есть константа MAX_WORKERS = min(32, (os.cpu_count() or 1) + 4)
        ЧТО ПРОВЕРЯЕМ: src/config.py должен возвращать значение из config.yaml
        КАК РАБОТАЕТ: Параметр должен быть числом и >= 1
        """
        from src.config import get_config
        
        config = get_config()
        max_workers = config["scan"]["max_workers"]
        
        assert isinstance(max_workers, int), \
            "max_workers должен быть целым числом"
        assert max_workers >= 1, \
            "max_workers должен быть >= 1"

    def test_dirs_to_scan_from_config(self):
        """
        Проверяем, что dirs_to_scan читается из config.yaml.
        
        ЗАЧЕМ: В syschange.py есть константа DIRS_TO_SCAN = ["/"]
        ЧТО ПРОВЕРЯЕМ: src/config.py должен возвращать список директорий
        КАК РАБОТАЕТ: Проверяем, что это список и он не пустой
        """
        from src.config import get_config
        
        config = get_config()
        dirs_to_scan = config["scan"]["dirs_to_scan"]
        
        assert isinstance(dirs_to_scan, list), \
            "dirs_to_scan должен быть списком"
        assert len(dirs_to_scan) > 0, \
            "dirs_to_scan не должен быть пустым"
        assert "/" in dirs_to_scan, \
            "dirs_to_scan должен содержать корневую ФС (/)"

    def test_excludes_from_config(self):
        """
        Проверяем, что excludes читается из config.yaml.
        
        ЗАЧЕМ: В syschange.py есть константа DEFAULT_EXCLUDES = ["/tmp", "/proc", ...]
        ЧТО ПРОВЕРЯЕМ: src/config.py должен возвращать список исключений
        КАК РАБОТАЕТ: Проверяем наличие ключевых директорий (/tmp, /proc, /sys)
        """
        from src.config import get_config
        
        config = get_config()
        excludes = config["excludes"]
        
        assert isinstance(excludes, list), \
            "excludes должен быть списком"
        
        # Проверяем наличие ключевых исключений
        required_excludes = ["/tmp", "/proc", "/sys", "/dev"]
        for exclude_path in required_excludes:
            assert exclude_path in excludes, \
                f"excludes должен содержать {exclude_path}"

    def test_binary_extensions_from_config(self):
        """
        Проверяем, что binary_extensions читается из config.yaml.
        
        ЗАЧЕМ: В syschange.py есть константа BINARY_EXTENSIONS = {'.png', '.jpg', ...}
        ЧТО ПРОВЕРЯЕМ: src/config.py должен возвращать список бинарных расширений
        КАК РАБОТАЕТ: Проверяем наличие распространённых расширений
        """
        from src.config import get_config
        
        config = get_config()
        binary_exts = config["binary_extensions"]
        
        assert isinstance(binary_exts, list), \
            "binary_extensions должен быть списком"
        
        # Проверяем наличие ключевых расширений
        required_exts = [".png", ".jpg", ".so", ".pyc"]
        for ext in required_exts:
            assert ext in binary_exts, \
                f"binary_extensions должен содержать {ext}"

    def test_git_config_from_config(self):
        """
        Проверяем, что Git-настройки читаются из config.yaml.
        
        ЗАЧЕМ: В syschange.py есть хардкод user.email и user.name для Git
        ЧТО ПРОВЕРЯЕМ: src/config.py должен возвращать Git-параметры
        КАК РАБОТАЕТ: Проверяем наличие enabled, user_email, user_name
        """
        from src.config import get_config
        
        config = get_config()
        git_config = config["git"]
        
        assert "enabled" in git_config, \
            "git.enabled должен присутствовать в конфиге"
        assert "user_email" in git_config, \
            "git.user_email должен присутствовать в конфиге"
        assert "user_name" in git_config, \
            "git.user_name должен присутствовать в конфиге"
        
        assert git_config["user_email"] == "snapshot@local", \
            "git.user_email должен быть 'snapshot@local'"

    def test_no_hardcoded_defaults_in_syschange(self):
        """
        КРИТИЧНЫЙ ТЕСТ: Проверяем, что syschange.py НЕ содержит хардкод-дефолтов.
        
        ЗАЧЕМ: Убедиться, что ВСЕ параметры читаются из config.yaml через src/config.py
        ЧТО ПРОВЕРЯЕМ: Отсутствие .get(key, default) для запрещённых ключей
        КАК РАБОТАЕТ: Парсим syschange.py через AST и ищем нарушения
        """
        project_root = Path(__file__).parent.parent
        syschange_file = project_root / "syschange.py"
        
        if not syschange_file.exists():
            pytest.skip("syschange.py не найден")
        
        violations = []
        
        with open(syschange_file, 'r', encoding='utf-8') as f:
            source = f.read()
            tree = ast.parse(source)
        
        # Ищем вызовы .get() с запрещенными ключами и дефолтами
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Проверяем, что это вызов метода .get()
                if (hasattr(node.func, 'attr') and 
                    node.func.attr == 'get' and 
                    len(node.args) >= 1):
                    
                    # Извлекаем имя ключа (первый аргумент)
                    if isinstance(node.args[0], ast.Constant):
                        key_name = node.args[0].value
                        
                        # Проверяем, запрещен ли этот ключ для дефолтов
                        if key_name in FORBIDDEN_CONFIG_KEYS_WITH_DEFAULTS:
                            # Если есть второй аргумент (дефолт) - нарушение!
                            if len(node.args) >= 2:
                                violations.append({
                                    "file": "syschange.py",
                                    "line": node.lineno,
                                    "key": key_name,
                                    "default": ast.unparse(node.args[1])
                                })
        
        if violations:
            error_msg = [
                "\n❌ НАЙДЕНЫ ХАРДКОД ДЕФОЛТЫ В syschange.py (нарушение docs/WORKFLOW.md):",
                "\nЛегальные дефолты должны быть ТОЛЬКО в config.yaml!\n"
            ]
            
            for v in violations:
                error_msg.append(
                    f"  📁 {v['file']}:{v['line']}\n"
                    f"     .get(\"{v['key']}\", {v['default']})  ← ЗАПРЕЩЕНО!\n"
                )
            
            error_msg.append(
                "\n💡 Как исправить:\n"
                "  1. Удалите второй аргумент из .get()\n"
                "  2. Добавьте значение в config.yaml\n"
                "  3. Используйте src/config.py для чтения параметров\n"
            )
            
            pytest.fail("".join(error_msg))

    def test_no_hardcoded_constants_in_syschange(self):
        """
        КРИТИЧНЫЙ ТЕСТ: Проверяем, что syschange.py НЕ содержит хардкод-констант.
        
        ЗАЧЕМ: Убедиться, что константы типа SNAPSHOT_BASE_DIR удалены из кода
        ЧТО ПРОВЕРЯЕМ: Отсутствие глобальных констант с запрещёнными именами
        КАК РАБОТАЕТ: Парсим syschange.py через AST и ищем присваивания
        """
        project_root = Path(__file__).parent.parent
        syschange_file = project_root / "syschange.py"
        
        if not syschange_file.exists():
            pytest.skip("syschange.py не найден")
        
        # Запрещённые имена констант (которые должны быть удалены)
        forbidden_constants = [
            "SNAPSHOT_BASE_DIR",
            "MAX_WORKERS",
            "DIRS_TO_SCAN",
            "DEFAULT_EXCLUDES",
            "MAX_TEXT_FILE_SIZE",
            "BINARY_EXTENSIONS",
        ]
        
        violations = []
        
        with open(syschange_file, 'r', encoding='utf-8') as f:
            source = f.read()
            tree = ast.parse(source)
        
        # Ищем глобальные присваивания с запрещёнными именами
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id in forbidden_constants:
                            violations.append({
                                "name": target.id,
                                "line": node.lineno
                            })
        
        if violations:
            error_msg = [
                "\n❌ НАЙДЕНЫ ХАРДКОД КОНСТАНТЫ В syschange.py (нарушение docs/WORKFLOW.md):",
                "\nВсе константы должны читаться из config.yaml через src/config.py!\n"
            ]
            
            for v in violations:
                error_msg.append(
                    f"  📁 syschange.py:{v['line']}\n"
                    f"     {v['name']} = ...  ← ЗАПРЕЩЕНО!\n"
                )
            
            error_msg.append(
                "\n💡 Как исправить:\n"
                "  1. Удалите константу из кода\n"
                "  2. Добавьте значение в config.yaml\n"
                "  3. Читайте через: config = get_config(); value = config[...]\n"
            )
            
            pytest.fail("".join(error_msg))

    def test_config_yaml_has_all_required_keys(self):
        """
        Проверяем, что config.yaml содержит все обязательные ключи.
        
        ЗАЧЕМ: Убедиться, что конфиг полный и валидный
        ЧТО ПРОВЕРЯЕМ: Наличие всех секций и обязательных параметров
        КАК РАБОТАЕТ: Загружаем config.yaml и проверяем структуру
        """
        from src.config import load_config
        
        config = load_config()
        
        # Проверка обязательных секций
        required_sections = ["logging", "scan", "git", "excludes", "binary_extensions"]
        for section in required_sections:
            assert section in config, \
                f"Отсутствует обязательная секция '{section}' в config.yaml"
        
        # Проверка параметров logging
        assert "level" in config["logging"], \
            "logging.level отсутствует в config.yaml"
        assert "use_colors" in config["logging"], \
            "logging.use_colors отсутствует в config.yaml"
        
        # Проверка параметров scan
        scan_params = ["snapshot_base_dir", "max_workers", "dirs_to_scan", 
                      "max_text_file_size", "min_parallel_size"]
        for param in scan_params:
            assert param in config["scan"], \
                f"scan.{param} отсутствует в config.yaml"
        
        # Проверка параметров git
        git_params = ["enabled", "user_email", "user_name"]
        for param in git_params:
            assert param in config["git"], \
                f"git.{param} отсутствует в config.yaml"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

