# Benchmark data

Datasets are not bundled with this repository. Use
`download_data.py` to fetch each benchmark from its public source.

## Layout after download

```
data/
├── README.md                       # this file
├── download_data.py                # downloader
├── ctf_archive.json                # CTF-Dojo task metadata
├── ctf-archive/                    # CTF challenge files
├── futurex/
│   ├── futurex_past.json           # FutureX past split (503 tasks)
│   └── futurex_online.json         # FutureX live split
└── polymarket_analysis.db          # PolyBench Polymarket DB
```

## Sources

| Benchmark | Source                                                         | Notes                                            |
|-----------|----------------------------------------------------------------|--------------------------------------------------|
| PolyBench | Polymarket public API                                          | Markets resolving Feb 6–22, 2026                 |
| CTF-Dojo  | https://github.com/pwncollege/ctf-archive                      | Requires `git clone`; ~2 GB                      |
| FutureX   | HuggingFace `futurex-ai/FutureX-Past`, `FutureX-Online`        | Auto-downloaded on first use (no manual step)    |

The CTF-Dojo benchmark additionally requires per-challenge Docker
images. The downloader records the image manifest; image builds are
performed lazily on first run by the solver sandbox.

## Usage

```bash
python data/download_data.py --benchmark polybench
python data/download_data.py --benchmark ctf_dojo
python data/download_data.py --benchmark futurex
```

Each call is idempotent: existing files are not re-downloaded.
