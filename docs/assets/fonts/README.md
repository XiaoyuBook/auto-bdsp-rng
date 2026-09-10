# Bundled UI fonts

The application loads **BDSP UI Sans Regular (400)** and **Medium (500)** with
Qt's application font API before constructing the UI. No system installation or
runtime download is needed. The private family name avoids matching a different
Noto version already installed on a user's computer. Technical monospace areas
retain their existing fonts. Missing files fall back to the existing UI font list
and produce a diagnostic through Python logging.

These are renamed derivatives of **Noto Sans SC 2.004**, from the official
[notofonts/noto-cjk repository](https://github.com/notofonts/noto-cjk/tree/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/SubsetOTF/SC):

- `Sans/SubsetOTF/SC/NotoSansSC-Regular.otf`
- `Sans/SubsetOTF/SC/NotoSansSC-Medium.otf`

Source commit: `f8d157532fbfaeda587e826d4cd5b21a49186f7c`.
Copyright © 2014–2021 Adobe (http://www.adobe.com/).
Distributed under the **SIL Open Font License 1.1**, reproduced in `OFL.txt` from
`Sans/LICENSE` at the same commit. Original copyright and license font metadata
are retained. Only family, face and unique-name metadata in the name/CFF tables
is changed; outlines, glyph coverage, metrics, hinting and OpenType features are
unchanged. No additional glyph subsetting is performed.

To rebuild, download the two upstream OTF files above into a temporary directory,
install the development-only `fonttools==4.64.0`, then run
`python scripts/prepare_ui_fonts.py <directory>`. The script verifies source
SHA-256 hashes before writing the derivatives. FontTools is not a runtime or
application build dependency.

Bundled SHA-256 hashes:

| File | SHA-256 |
| --- | --- |
| BDSPUISans-Regular.otf | `43981dd417f045de9691b1e1576cf1829572db343941a6ae965ecce5b1477b29` |
| BDSPUISans-Medium.otf | `3577d4bed1ecc27d321a675936740df6643687ebac02f620cee13c93bc710626` |

The existing PyInstaller `docs/assets` collection and release-copy rules include
both faces and this license. The two font files total about **15.9 MiB** before archive
compression. An ordinary application build uses these committed files offline.
