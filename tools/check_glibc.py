"""Проверяет реальный потолок GLIBC упакованных ELF, а не имя образа сборки."""
import re
import subprocess
import sys
from pathlib import Path

maximum = (0, 0)
for path in Path(sys.argv[1]).rglob("*"):
    if not path.is_file():
        continue
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            continue
    output = subprocess.run(["objdump", "-T", str(path)], capture_output=True,
                            text=True, check=True).stdout
    for major, minor in re.findall(r"GLIBC_([0-9]+)\.([0-9]+)", output):
        maximum = max(maximum, (int(major), int(minor)))
if maximum == (0, 0) or maximum > (2, 35):
    raise SystemExit(f"Неподдерживаемое требование GLIBC: {maximum}")
print(f"GLIBC <= {maximum[0]}.{maximum[1]}")
