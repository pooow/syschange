#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль для работы с конфигурацией проекта syschange.
Обеспечивает загрузку config.yaml и доступ к параметрам.

Правило: Все настройки должны быть явно указаны в config.yaml.
Хардкод дефолтов запрещен согласно docs/WORKFLOW.md.
"""

import yaml
from pathlib import Path
from typing import Any, Dict


# Глобальная переменная для кэширования конфигурации
# Загружается один раз при первом вызове get_config()
_CONFIG_CACHE: Dict[str, Any] = None


def load_config(config_path: Path = None) -> Dict[str, Any]:
    """
    Загружает настройки из YAML файла.
    
    Args:
        config_path: Путь к config.yaml (по умолчанию: ./config.yaml)
    
    Returns:
        Словарь с конфигурацией
    
    Raises:
        FileNotFoundError: Если config.yaml не найден
    """
    if config_path is None:
        # По умолчанию ищем config.yaml в корне проекта
        config_path = Path(__file__).parent.parent / "config.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            f"Please create it based on config.yaml.example"
        )
    
    try:
        with config_path.open('r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            if config is None:
                raise ValueError("config.yaml is empty")
            return config
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing config.yaml: {e}")


def get_config(config_path: Path = None) -> Dict[str, Any]:
    """
    Возвращает полную конфигурацию из config.yaml.
    
    Кэширует конфигурацию при первом вызове для производительности.
    Используется в syschange.py для получения всех параметров.
    
    Args:
        config_path: Путь к config.yaml (опционально, для тестов)
    
    Returns:
        Полный словарь конфигурации
    
    Raises:
        FileNotFoundError: Если config.yaml не найден
        ValueError: Если конфиг невалиден
    
    Examples:
        >>> config = get_config()
        >>> config["scan"]["snapshot_base_dir"]
        '/var/log/system_changes'
    """
    global _CONFIG_CACHE
    
    # Если конфиг уже загружен и путь не переопределён - возвращаем кэш
    if _CONFIG_CACHE is not None and config_path is None:
        return _CONFIG_CACHE
    
    # Загружаем конфигурацию
    config = load_config(config_path)
    
    # Валидируем обязательные параметры
    validate_config(config)
    
    # Кэшируем только при стандартном пути (не для тестов)
    if config_path is None:
        _CONFIG_CACHE = config
    
    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """
    Проверяет наличие обязательных параметров в конфигурации.
    
    Args:
        config: Словарь конфигурации
    
    Returns:
        True если конфиг валиден
    
    Raises:
        ValueError: Если отсутствуют обязательные параметры
    """
    # Обязательные параметры (путь через точку: секция.параметр)
    required_params = [
        "scan.snapshot_base_dir",
        "scan.max_workers",
        "scan.dirs_to_scan"
    ]
    
    missing = []
    for param_path in required_params:
        try:
            get_param(config, param_path)
        except ValueError:
            missing.append(param_path)
    
    if missing:
        raise ValueError(
            f"Missing required parameters in config.yaml: {', '.join(missing)}. "
            f"Please add them to the configuration file."
        )
    
    return True


def get_param(config: Dict[str, Any], param_path: str) -> Any:
    """
    Извлекает параметр из конфигурации по пути (поддержка вложенных ключей).
    
    Args:
        config: Словарь конфигурации
        param_path: Путь к параметру через точку (например, "scan.snapshot_base_dir")
    
    Returns:
        Значение параметра
    
    Raises:
        ValueError: Если параметр не найден
    
    Examples:
        >>> config = {"scan": {"snapshot_base_dir": "/var/log"}}
        >>> get_param(config, "scan.snapshot_base_dir")
        '/var/log'
    """
    keys = param_path.split('.')
    value = config
    
    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        raise ValueError(
            f"Parameter '{param_path}' not found in config.yaml. "
            f"Please add it to the configuration file."
        )

