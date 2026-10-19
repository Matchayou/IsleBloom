# Real parser fixtures

Generated locally with Pillow 12.3.0. These are actual input files, not saved parser outputs.

- `chinese-scan.png`: raster page reading 公司甲 营业收入 12345 万元, rendered with macOS STHeiti Medium; tests no longer require the font.
- `partial-scan.pdf`: two image-only PDF pages, first containing ACME revenue 12345 and second blank. The blank page deliberately cannot yield located text and is reported missing, not accepted as success.
- `broken.pdf`: truncated PDF header, expected parse failure with retained original bytes.
