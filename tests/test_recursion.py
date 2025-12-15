import pytest
from pathlib import Path
import shutil
from src.config import get_config
# Импортируем функцию, которую будем тестировать (предполагаем, что она в syschange.py)
# Для теста нам придется импортировать scan_filesystem из syschange
import sys
sys.path.append(str(Path(__file__).parent.parent))
from syschange import scan_filesystem

class TestRecursion:
    @pytest.fixture
    def setup_dirs(self, tmp_path):
        """Создает структуру:
        /root_scan
          /file.txt
          /snapshots (output dir)
            /fs_before.txt
        """
        root_scan = tmp_path / "root_scan"
        root_scan.mkdir()
        
        # Обычный файл
        (root_scan / "file.txt").write_text("content")
        
        # Директория снимков ВНУТРИ сканируемой зоны
        snapshot_dir = root_scan / "snapshots"
        snapshot_dir.mkdir()
        (snapshot_dir / "fs_before.txt").write_text("snapshot data")
        
        return root_scan, snapshot_dir

    def test_scan_excludes_snapshot_dir_automatically(self, setup_dirs):
        """
        Тест должен упасть, если сканер найдет файлы внутри snapshot_dir.
        """
        root_scan, snapshot_dir = setup_dirs
        
        # Конфиг-мок
        config = {
            "scan": {
                "min_parallel_size": 1000,
                "max_workers": 1,
                "max_text_file_size": 1024,
                "snapshot_base_dir": str(snapshot_dir) # Указываем, что база снимков здесь
            },
            "binary_extensions": [],
            "excludes": [] # Явных исключений нет
        }
        
        # Запускаем сканирование
        # Важно: передаем snapshot_dir как аргумент, если мы изменим сигнатуру,
        # или ожидаем, что логика исключения будет внутри scan_filesystem или main.
        # Поскольку сейчас логика исключений передается снаружи, этот тест проверяет
        # поведение "как есть" и "как должно быть".
        
        # В текущей реализации (syschange.py) мы просто передаем excludes. 
        # Чтобы тест был честным "интеграционным" для логики защиты, 
        # нам нужно проверить функцию, которая формирует параметры (сейчас это main, 
        # но тестировать main сложно).
        
        # ПОЭТОМУ: Мы изменим подход. Мы добавим логику авто-исключения внутрь scan_filesystem
        # или подготовительной функции.
        
        results = scan_filesystem(
            base_dirs=[str(root_scan)],
            excludes=[], # Не исключаем вручную
            config=config,
            collect_hashes=False
        )
        
        # Проверяем, что нашли file.txt
        found_files = [str(f.path) for f in results]
        assert any("file.txt" in f for f in found_files)
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: Не должны найти ничего из папки snapshots
        files_in_snapshot = [f for f in found_files if "snapshots" in f]
        
        if files_in_snapshot:
            pytest.fail(f"ОШИБКА: Сканер нашел файлы внутри собственной папки снимков: {files_in_snapshot}")


