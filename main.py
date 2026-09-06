"""PyInstaller entry point. Keeps the frozen build off relative imports."""

from mvcclone.gui import main

if __name__ == "__main__":
    main()
