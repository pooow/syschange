#!/bin/bash
set -e  # Прерывать выполнение при ошибках

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Запуск E2E теста для syschange ===${NC}"

# 1. Создаем изолированное окружение
TEST_DIR=$(mktemp -d /tmp/syschange_e2e_XXXXXX)
PROJECT_ROOT=$(pwd)

echo "Рабочая директория: $TEST_DIR"

# Копируем проект в тест-директорию
cp "$PROJECT_ROOT/syschange.py" "$TEST_DIR/"
cp -r "$PROJECT_ROOT/src" "$TEST_DIR/"

# Создаем директории для сканирования
mkdir -p "$TEST_DIR/scan_target/subdir"
mkdir -p "$TEST_DIR/snapshots"

# 2. Создаем тестовые файлы
echo "Original content" > "$TEST_DIR/scan_target/file1.txt"
echo "Subdir content" > "$TEST_DIR/scan_target/subdir/subfile.txt"
# Бинарный файл (имитация)
dd if=/dev/urandom of="$TEST_DIR/scan_target/image.jpg" bs=1024 count=1 2>/dev/null

# 3. Создаем тестовый конфиг (config.yaml)
# ВАЖНО: Не исключаем /tmp, так как тест работает внутри /tmp!
cat <<EOF > "$TEST_DIR/config.yaml"
logging:
  level: "DEBUG"
  use_colors: false

scan:
  snapshot_base_dir: "$TEST_DIR/snapshots"
  max_workers: 2
  dirs_to_scan:
    - "$TEST_DIR/scan_target"
  max_text_file_size: 1048576
  min_parallel_size: 1048576

excludes:
  - "/var/tmp"
  - "*.tmp"

git:
  enabled: true
  user_email: "test@local"
  user_name: "Test User"

binary_extensions:
  - ".jpg"
  - ".png"
EOF

# Переходим в тест-директорию
cd "$TEST_DIR"

# 4. Запускаем BEFORE
echo -e "\n${GREEN}[Step 1] Running 'before'...${NC}"
python3 syschange.py before test_session

# Проверки после BEFORE
if [ ! -d "snapshots/test_session/fs_git/.git" ]; then
    echo -e "${RED}ERROR: Git repo not created!${NC}"
    exit 1
fi
if [ ! -f "snapshots/test_session/fs_before.txt" ]; then
    echo -e "${RED}ERROR: fs_before.txt missing!${NC}"
    exit 1
fi

# 5. Вносим изменения
echo -e "\n${GREEN}[Step 2] Making changes...${NC}"
# Изменение файла
echo "Modified content" >> "scan_target/file1.txt"
# Новый файл
echo "New file content" > "scan_target/new_file.txt"
# Удаление файла
rm "scan_target/subdir/subfile.txt"

# 6. Запускаем AFTER
echo -e "\n${GREEN}[Step 3] Running 'after'...${NC}"
python3 syschange.py after test_session

# 7. Проверяем отчет
REPORT="snapshots/test_session/full_report.txt"

if [ ! -f "$REPORT" ]; then
    echo -e "${RED}ERROR: Report file missing!${NC}"
    exit 1
fi

echo -e "\n${GREEN}[Step 4] Verifying report content...${NC}"
cat "$REPORT"

# Проверяем наличие ключевых изменений в отчете
if grep -q "scan_target/file1.txt" "$REPORT"; then
    echo "OK: file1.txt change detected"
else
    echo -e "${RED}FAIL: file1.txt change NOT detected${NC}"
    exit 1
fi

if grep -q "scan_target/new_file.txt" "$REPORT"; then
    echo "OK: new_file.txt creation detected"
else
    echo -e "${RED}FAIL: new_file.txt creation NOT detected${NC}"
    exit 1
fi

if grep -q "scan_target/subdir/subfile.txt" "$REPORT"; then
    echo "OK: subfile.txt deletion detected"
else
    echo -e "${RED}FAIL: subfile.txt deletion NOT detected${NC}"
    exit 1
fi

echo -e "\n${GREEN}=== SUCCESS: E2E Test Passed! ===${NC}"

# Очистка
rm -rf "$TEST_DIR"

