#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тесты для модуля src/config.py.
Проверяют загрузку конфигурации, валидацию обязательных параметров.
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from src.config import load_config, validate_config, get_param


def test_config_loads_from_yaml(tmp_path):
    """
    Проверяет, что конфиг успешно загружается из config.yaml.
    
    Зачем: Убедиться, что load_config() читает YAML без ошибок.
    Что проверяем: Возвращается словарь с ожидаемой структурой.
    Как работает: Создаём временный config.yaml, загружаем его.
    """
    config_file = tmp_path / "config.yaml"
    config_data = {
        "logging": {"level": "INFO"},
        "scan": {
            "snapshot_base_dir": "/var/log/system_changes",
            "max_workers": 16
        }
    }
    config_file.write_text(yaml.dump(config_data))
    
    config = load_config(config_file)
    
    assert "logging" in config
    assert "scan" in config
    assert config["scan"]["snapshot_base_dir"] == "/var/log/system_changes"
    assert config["scan"]["max_workers"] == 16


def test_config_missing_file_raises_error():
    """
    Проверяет, что отсутствие config.yaml выбрасывает FileNotFoundError.
    
    Зачем: Явная ошибка лучше, чем молчаливый возврат пустого словаря.
    Что проверяем: FileNotFoundError с понятным сообщением.
    Как работает: Передаём несуществующий путь в load_config().
    """
    with pytest.raises(FileNotFoundError, match="config.yaml not found"):
        load_config(Path("/nonexistent/config.yaml"))


def test_validate_config_checks_required_params():
    """
    Проверяет, что validate_config() требует обязательные параметры.
    
    Зачем: Без snapshot_base_dir скрипт не может работать.
    Что проверяем: ValueError при отсутствии обязательного параметра.
    Как работает: Передаём неполный конфиг в validate_config().
    """
    incomplete_config = {
        "logging": {"level": "INFO"}
        # Отсутствует scan.snapshot_base_dir
    }
    
    with pytest.raises(ValueError, match="snapshot_base_dir"):
        validate_config(incomplete_config)


def test_validate_config_accepts_valid_config():
    """
    Проверяет, что валидный конфиг проходит валидацию без ошибок.
    
    Зачем: Позитивный тест для валидации.
    Что проверяем: validate_config() возвращает True для корректного конфига.
    Как работает: Передаём полный конфиг с всеми обязательными параметрами.
    """
    valid_config = {
        "logging": {"level": "INFO"},
        "scan": {
            "snapshot_base_dir": "/var/log/system_changes",
            "max_workers": 16,
            "dirs_to_scan": ["/"]
        },
        "excludes": ["/tmp", "/proc"]
    }
    
    assert validate_config(valid_config) is True


def test_get_param_returns_value():
    """
    Проверяет, что get_param() возвращает значение из конфига.
    
    Зачем: Единая точка доступа к параметрам.
    Что проверяем: Корректное извлечение вложенных параметров (scan.snapshot_base_dir).
    Как работает: Передаём конфиг и путь к параметру ("scan.snapshot_base_dir").
    """
    config = {
        "scan": {
            "snapshot_base_dir": "/var/log/system_changes"
        }
    }
    
    value = get_param(config, "scan.snapshot_base_dir")
    assert value == "/var/log/system_changes"


def test_get_param_raises_on_missing_key():
    """
    Проверяет, что get_param() выбрасывает ValueError для отсутствующего ключа.
    
    Зачем: Явная ошибка вместо None или KeyError.
    Что проверяем: ValueError с понятным сообщением (какой параметр не найден).
    Как работает: Запрашиваем несуществующий параметр.
    """
    config = {
        "scan": {
            "snapshot_base_dir": "/var/log/system_changes"
        }
    }
    
    with pytest.raises(ValueError, match="Parameter 'scan.nonexistent' not found"):
        get_param(config, "scan.nonexistent")

