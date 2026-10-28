"""Package the add-on as an installable zip: python tools/build_zip.py"""

import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "anime_studio")


def main():
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "anime_studio.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _dirs, files in os.walk(SRC):
            if "__pycache__" in folder:
                continue
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(folder, f)
                z.write(path, os.path.relpath(path, ROOT))
    print("Wrote", out)


if __name__ == "__main__":
    main()
