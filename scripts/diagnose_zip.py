"""Quick diagnostic: prints the full file listing inside data/gtfs.zip so we
can see the actual folder/file structure before fixing build_database.py."""

import zipfile
from pathlib import Path

zip_path = Path(__file__).resolve().parent.parent / "data" / "gtfs.zip"

with zipfile.ZipFile(zip_path) as zf:
    names = zf.namelist()
    print(f"Total entries: {len(names)}\n")

    # Show everything inside branch folder "2" (metro_train) as a representative sample
    print("--- Contents of folder '2/' (metro_train) ---")
    for n in names:
        if n.startswith("2/"):
            print(n)

    print("\n--- First 30 entries overall ---")
    for n in names[:30]:
        print(n)
