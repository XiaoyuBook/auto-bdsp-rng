# Bundled UI fonts

The application uses **MiSans Regular** for body text and **MiSans Medium** for
headings and key numbers. Both original OpenType faces are registered through
Qt before constructing the UI, without a system installation or runtime download.
Technical monospace areas keep their existing fonts. Missing assets fall back to
the UI font list and produce a diagnostic through Python logging.

## Source and attribution

- Official download page: https://hyperos.mi.com/font/zh/download/
- Official archive: https://hyperos.mi.com/font-download/MiSans.zip
- Archive entries: `MiSans/otf/MiSans-Regular.otf` and `MiSans/otf/MiSans-Medium.otf`
- Font version: **4.009**, downloaded 2026-09-10.
- Archive SHA-256: `b6aa1fc827035922612df8edf36e5609bca1c5441e25cd57572204569b7b81d9`.

Copyright © 2020-2025 Beijing Xiaomi Mobile Software Co.,Ltd. All Rights Reserved.
Design: Beijing Xiaomi Mobile Software Co.,Ltd & Hanyi Fonts.

MiSans is provided under its own **MiSans Font Intellectual Property License
Agreement**, retained in `MiSans-LICENSE.pdf` from the
[official license](https://hyperos.mi.com/font-download/MiSans字体知识产权许可协议.pdf).
The application's GPL license does not relicense these font assets. The About
page explicitly credits MiSans. These assets accompany the application; do not
distribute them as a standalone font package. Consult the complete agreement for
its terms, including attribution, copyright retention and restrictions on modification.

Both font files are byte-for-byte copies of the official files: no renaming of
internal family names, subsetting, conversion, or other modification is performed.

## Face selection and importing

MiSans 4.009 has native OS/2 weight values **330 (Regular)** and **380 (Medium)**.
The shared `ui_font()` and `ui_styles()` helpers map semantic UI weights 400/500
to these native values, preventing Qt from selecting Medium for ordinary body
text. Technical monospace styles retain their weights. The `tnum` OpenType
feature keeps live numbers at a stable width. Tests inspect actual glyph runs
and compare their outlines and metrics to the bundled originals.

To import again, extract the two original OTF files above into a temporary
directory, download the official license into it as `MiSans-LICENSE.pdf`, then run
`python scripts/prepare_ui_fonts.py <directory>`. The script validates all three
SHA-256 hashes before copying any file. It requires only the Python standard
library. Application builds use the committed assets offline.

| File | SHA-256 |
| --- | --- |
| MiSans-Regular.otf | `8e9caa6f34f27c6baad1ecf0058cc13e1801efff698bc23e6e0b095d9a9ed9cb` |
| MiSans-Medium.otf | `f9239c75fa0a661b788916250c6d4ad0d52d8a643f1ce2a9142f6588d4d2cb64` |
| MiSans-LICENSE.pdf | `4a93a27cd2bd81b3b5ecfd0a853144a876fa26938a93a68443c67d74172fcb86` |

The existing PyInstaller `docs/assets` collection and release-copy rules include
both faces, this README and the license. The font files total about **12.5 MiB**
before archive compression.
