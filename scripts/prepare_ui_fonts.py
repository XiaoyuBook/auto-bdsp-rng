"""Rebuild the bundled UI faces; requires fonttools==4.64.0 only when rebuilding.

Download the pinned upstream OTFs into a directory and pass that directory as the
sole argument. Glyphs, hinting, metrics and OpenType features are left intact.
"""

from hashlib import sha256
from pathlib import Path
import sys

from fontTools.ttLib import TTFont


SOURCE_COMMIT = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
SOURCE_HASHES = {
    "Regular": "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9",
    "Medium": "7633f5a016d4dd95e685a69633d818aabc4644c4b08e26bd35b1b30c45ed5dda",
}


def prepare(source_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for style, expected_hash in SOURCE_HASHES.items():
        source = source_dir / f"NotoSansSC-{style}.otf"
        if sha256(source.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Unexpected upstream font: {source}")
        font = TTFont(source, recalcTimestamp=False)
        family = "BDSP UI Sans"
        postscript = f"BDSPUISans-{style}"
        # Keep legacy Medium family grouping for Windows; Qt uses the preferred
        # family/subfamily records to select actual 400 and 500 faces.
        names = {
            1: family if style == "Regular" else f"{family} {style}",
            2: "Regular",
            3: f"2.004;BDSP;{postscript}",
            4: f"{family} {style}",
            6: postscript,
            16: family,
            17: style,
        }
        records = font["name"]
        for record in records.names:
            if record.nameID in names:
                record.string = names[record.nameID].encode(record.getEncoding())
        for name_id, value in names.items():
            records.setName(value, name_id, 3, 1, 0x409)
        cff = font["CFF "].cff
        cff.fontNames = [postscript]
        cff.topDictIndex[0].FamilyName = family
        cff.topDictIndex[0].FullName = f"{family} {style}"
        target = output_dir / f"{postscript}.otf"
        font.save(target)
        font.close()
        print(f"{target.name}: {sha256(target.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(__file__).resolve().parents[1] / "docs/assets/fonts")
