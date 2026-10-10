"""Download the two open datasets used for real-data validation.

    python scripts/fetch_data.py

  MIMIC-IV Clinical Database Demo v2.2   PhysioNet, ODbL 1.0, open access
      100 de-identified ICU patients.  Only the tables used are fetched.
  Crowd-sourced Fitbit datasets          Zenodo 10.5281/zenodo.53894, CC BY 4.0
      ~30 consumer-band users, April-May 2016.

Files land in data/ (gitignored) and are checked against the checksums the
publishers provide.  Re-running skips anything already present and valid.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

MIMIC_BASE = "https://physionet.org/files/mimic-iv-demo/2.2/"
MIMIC_FILES = [
    "hosp/patients.csv.gz",
    "hosp/admissions.csv.gz",
    "hosp/diagnoses_icd.csv.gz",
    "hosp/d_icd_diagnoses.csv.gz",
    "hosp/labevents.csv.gz",
    "hosp/d_labitems.csv.gz",
    "icu/icustays.csv.gz",
    "icu/chartevents.csv.gz",
    "icu/d_items.csv.gz",
    "icu/inputevents.csv.gz",
    "icu/outputevents.csv.gz",
    "LICENSE.txt",
]

FITBIT_ZIP = "mturkfitbit_export_4.12.16-5.12.16.zip"
FITBIT_URL = f"https://zenodo.org/api/records/53894/files/{FITBIT_ZIP}/content"
FITBIT_MD5 = "7afbecdce29814e1be2e9a7c94f8f165"
FITBIT_KEEP = (
    "heartrate_seconds_merged.csv",
    "dailyActivity_merged.csv",
    "sleepDay_merged.csv",
    "weightLogInfo_merged.csv",
)

UA = {"User-Agent": "prahari-fetch/1.0"}


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        return r.read()


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_mimic() -> Path:
    dest = DATA / "mimic-iv-demo"
    sums = {}
    for line in _get(MIMIC_BASE + "SHA256SUMS.txt").decode().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name.strip()] = digest
    for name in MIMIC_FILES:
        out = dest / name
        if out.exists() and _digest(out, "sha256") == sums[name]:
            print(f"  ok      mimic {name}")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_get(MIMIC_BASE + name))
        if _digest(out, "sha256") != sums[name]:
            out.unlink()
            raise SystemExit(f"checksum mismatch: {name}")
        print(f"  fetched mimic {name}  ({out.stat().st_size // 1024} KB)")
    return dest


def fetch_fitbit() -> Path:
    dest = DATA / "fitbit"
    if all((dest / f).exists() for f in FITBIT_KEEP):
        print("  ok      fitbit")
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    zpath = DATA / FITBIT_ZIP
    if not zpath.exists() or _digest(zpath, "md5") != FITBIT_MD5:
        zpath.write_bytes(_get(FITBIT_URL))
        if _digest(zpath, "md5") != FITBIT_MD5:
            zpath.unlink()
            raise SystemExit("checksum mismatch: fitbit zip")
    with zipfile.ZipFile(zpath) as z:
        for member in z.namelist():
            name = Path(member).name
            if name in FITBIT_KEEP:
                (dest / name).write_bytes(z.read(member))
                print(f"  fetched fitbit {name}")
    zpath.unlink()
    return dest


def main() -> None:
    print("MIMIC-IV Clinical Database Demo v2.2 (PhysioNet, ODbL)")
    fetch_mimic()
    print("Crowd-sourced Fitbit data (Zenodo 10.5281/zenodo.53894, CC BY 4.0)")
    fetch_fitbit()
    print(f"\nData in {DATA.relative_to(ROOT)}/")


if __name__ == "__main__":
    sys.exit(main())
