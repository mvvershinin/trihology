"""
Точка входа для PyInstaller-сборки Phototrichogram.

Отдельный файл-launcher в корне репозитория (а не phototrichogram/__main__.py)
нужен, чтобы PyInstaller корректно добавил корень репозитория в sys.path и
разрешил абсолютный импорт `phototrichogram.main` как импорт пакета, а не
скрипта внутри самого пакета. См. build.spec.
"""

from phototrichogram.main import main

if __name__ == "__main__":
    main()
