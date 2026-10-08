# Vendored readers

These MIT-licensed modules are kept as source to avoid native LevelDB dependencies on Windows. Copyright and license notices remain in each module.

- `ccl_leveldb.py`: https://github.com/cclgroupltd/ccl_chromium_reader at `ef840de30221c4d65bc96d2f4d9057e9ef2f526d`, original `ccl_chromium_reader/storage_formats/ccl_leveldb.py`. The only local change is its Snappy import path.
- `snappy.py`: https://github.com/cclgroupltd/ccl_simplesnappy at `3d085230baa8c46cf2090ebba29bf6e8eab31087`, original `ccl_simplesnappy/ccl_simplesnappy.py`, unchanged.

The CCL reader can expose historical records. Our Edge adapter restricts files using the CURRENT manifest, replays sequence numbers, honors deletion tombstones, and uses only current logical records. Raw forensic output is never used as a workspace export.
