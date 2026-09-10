"""Import original MiSans 4.009 faces and license, using the standard library only.

Put MiSans/otf/MiSans-{Regular,Medium}.otf from the official MiSans.zip and the
license PDF (named MiSans-LICENSE.pdf) in one directory, then pass it as the sole
argument. See docs/assets/fonts/README.md for sources and archive hash.
Never rename internal font families, subset, or otherwise modify these files.
"""

from hashlib import sha256
from pathlib import Path
import sys


SOURCE_HASHES = {
    "MiSans-Regular.otf": "8e9caa6f34f27c6baad1ecf0058cc13e1801efff698bc23e6e0b095d9a9ed9cb",
    "MiSans-Medium.otf": "f9239c75fa0a661b788916250c6d4ad0d52d8a643f1ce2a9142f6588d4d2cb64",
    "MiSans-LICENSE.pdf": "4a93a27cd2bd81b3b5ecfd0a853144a876fa26938a93a68443c67d74172fcb86",
}


def prepare(source_dir: Path, output_dir: Path) -> None:
    files = {}
    for filename, expected_hash in SOURCE_HASHES.items():
        data = (source_dir / filename).read_bytes()
        if sha256(data).hexdigest() != expected_hash:
            raise ValueError(f"Unexpected upstream file: {source_dir / filename}")
        files[filename] = data
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, data in files.items():
        target = output_dir / filename
        target.write_bytes(data)
        print(f"{target.name}: {sha256(data).hexdigest()}")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(__file__).resolve().parents[1] / "docs/assets/fonts")
