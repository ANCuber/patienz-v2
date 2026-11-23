#!/usr/bin/env python3
"""Resumable downloader for the 12 NIH ChestX-ray14 image archives.

The official batch_download_zips.py shipped by NIH calls urlretrieve() once per
file with no timeout, no retry and no resume: each archive is 2-4 GB, so a
single dropped connection loses the whole file and the script silently leaves a
truncated .tar.gz behind.  This tool does the same download but

  * resumes with an HTTP Range request from whatever bytes are already on disk,
  * retries each archive until it completes (a stalled socket is killed by a
    read timeout instead of hanging forever),
  * writes to <name>.part and only renames on a clean finish, so a half file can
    never be mistaken for a good one,
  * optionally extracts (and deletes) each archive as soon as it lands, which
    keeps the peak disk need at ~45 GB instead of ~87 GB.

Stdlib only.  Uses curl when available (faster, better proxy/TLS handling on
Windows) and falls back to urllib otherwise.  Safe to re-run: finished archives
are skipped, partial ones continue where they stopped.

Usage (PowerShell, from the repo root):
  python tools/download_nih_cxr.py --dest C:\nih-cxr
  python tools/download_nih_cxr.py --dest C:\nih-cxr --extract --delete-archives
"""
import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

# The 12 Box links published by the NIH Clinical Center alongside
# ChestXray-NIHCC (identical to their batch_download_zips.py).
LINKS = [
    "https://nihcc.box.com/shared/static/vfk49d74nhbxq3nqjg0900w5nvkorp5c.gz",
    "https://nihcc.box.com/shared/static/i28rlmbvmfjbl8p2n3ril0pptcmcu9d1.gz",
    "https://nihcc.box.com/shared/static/f1t00wrtdk94satdfb9olcolqx20z2jp.gz",
    "https://nihcc.box.com/shared/static/0aowwzs5lhjrceb3qp67ahp0rd1l1etg.gz",
    "https://nihcc.box.com/shared/static/v5e3goj22zr6h8tzualxfsqlqaygfbsn.gz",
    "https://nihcc.box.com/shared/static/asi7ikud9jwnkrnkj99jnpfkjdes7l6l.gz",
    "https://nihcc.box.com/shared/static/jn1b4mw4n6lnh74ovmcjb8y48h8xj07n.gz",
    "https://nihcc.box.com/shared/static/tvpxmn7qyrgl0w8wfh9kqfjskv6nmm1j.gz",
    "https://nihcc.box.com/shared/static/upyy3ml7qdumlgk2rfcvlb9k6gvqq2pj.gz",
    "https://nihcc.box.com/shared/static/l6nilvfa9cg3s28tqv1qc1olm3gnz54p.gz",
    "https://nihcc.box.com/shared/static/hhq8fkdgvcari67vfhs7ppg2w6ni4jze.gz",
    "https://nihcc.box.com/shared/static/ioqwiy20ihqwyr8pf4c24eazhh281pbu.gz",
]
EXPECTED_IMAGES = 112120          # ChestX-ray14: 112,120 frontal views
UA = "Mozilla/5.0 (compatible; patienz-image-bank/1.0)"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def remote_size(url):
    """Total size in bytes, or None when the server will not say.

    Box answers HEAD on these shared/static links with 404, so ask for one byte
    instead and read the total out of Content-Range ("bytes 0-0/2008470987").
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range") or ""
            tail = cr.rsplit("/", 1)[-1].strip() if "/" in cr else ""
            if tail.isdigit():
                return int(tail)
            n = r.headers.get("Content-Length")      # server ignored Range: this is the whole file
            return int(n) if n and r.status == 200 else None
    except Exception:
        return None


def curl_path():
    return shutil.which("curl.exe") or shutil.which("curl")


def fetch_curl(url, part, curl):
    """Resume into `part` with curl. Returns True when curl exits clean."""
    cmd = [curl, "-L", "-C", "-", "--fail",
           "--speed-time", "60", "--speed-limit", "1024",   # abort a stalled socket
           "--connect-timeout", "60", "-A", UA,
           "-o", part, url]
    return subprocess.call(cmd) == 0


def fetch_urllib(url, part, total):
    """Resume into `part` with a Range request. Returns True when complete."""
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = "bytes=%d-" % have
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        # 200 means the server ignored our Range: restart from zero.
        mode = "ab" if r.status == 206 else "wb"
        if mode == "wb":
            have = 0
        with open(part, mode) as fh:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                have += len(chunk)
                if total:
                    sys.stdout.write("\r    %s / %s (%.1f%%)   "
                                     % (human(have), human(total), 100.0 * have / total))
                else:
                    sys.stdout.write("\r    %s   " % human(have))
                sys.stdout.flush()
    sys.stdout.write("\n")
    return total is None or os.path.getsize(part) >= total


def download_one(url, dest, name, attempts, curl):
    final = os.path.join(dest, name)
    part = final + ".part"
    total = remote_size(url)
    if os.path.exists(final) and (total is None or os.path.getsize(final) == total):
        print("  %s already complete (%s), skipping" % (name, human(os.path.getsize(final))))
        return True
    for attempt in range(1, attempts + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        print("  attempt %d/%d, resuming at %s%s"
              % (attempt, attempts, human(have),
                 (" of " + human(total)) if total else ""))
        try:
            ok = fetch_curl(url, part, curl) if curl else fetch_urllib(url, part, total)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print("    interrupted: %s" % exc)
            ok = False
        got = os.path.getsize(part) if os.path.exists(part) else 0
        if ok and (total is None or got >= total):
            os.replace(part, final)
            print("  %s done (%s)" % (name, human(got)))
            return True
        if got == have and attempt > 1:
            time.sleep(min(60, 5 * attempt))    # no progress at all: back off harder
        else:
            time.sleep(5)
    print("  %s FAILED after %d attempts; re-run to continue from %s"
          % (name, attempts, human(os.path.getsize(part) if os.path.exists(part) else 0)))
    return False


def extract(archive, dest):
    print("  extracting %s ..." % os.path.basename(archive))
    with tarfile.open(archive, "r:gz") as tf:      # gzip CRC fails loudly on a truncated file
        tf.extractall(dest)


def count_images(dest):
    n = 0
    for _root, _dirs, files in os.walk(dest):
        n += sum(1 for f in files if f.lower().endswith(".png"))
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", required=True,
                    help="download directory, OUTSIDE the repo (needs ~45-90 GB)")
    ap.add_argument("--only", default="",
                    help="comma-separated archive numbers, e.g. 1,2,3 (default: all 12)")
    ap.add_argument("--attempts", type=int, default=50,
                    help="retries per archive before giving up (default 50)")
    ap.add_argument("--extract", action="store_true",
                    help="untar each archive as soon as it finishes")
    ap.add_argument("--delete-archives", action="store_true",
                    help="with --extract, delete the .tar.gz after a clean extract")
    ap.add_argument("--no-curl", action="store_true", help="force the urllib path")
    args = ap.parse_args()

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    curl = None if args.no_curl else curl_path()
    print("destination : %s" % dest)
    print("transport   : %s" % ("curl (%s)" % curl if curl else "urllib"))
    free = shutil.disk_usage(dest).free
    print("free space  : %s" % human(free))
    if free < 50 * 1024 ** 3:
        print("WARNING: ChestX-ray14 needs ~45 GB extracted (~87 GB if you keep the archives).")

    wanted = ([int(x) for x in args.only.split(",") if x.strip()]
              if args.only else list(range(1, len(LINKS) + 1)))
    failed = []
    for idx in wanted:
        name = "images_%03d.tar.gz" % idx
        print("\n[%d/%d] %s" % (idx, len(LINKS), name))
        if not download_one(LINKS[idx - 1], dest, name, args.attempts, curl):
            failed.append(name)
            continue
        if args.extract:
            try:
                extract(os.path.join(dest, name), dest)
            except Exception as exc:
                print("  extract FAILED (%s) - archive is corrupt, deleting so a re-run refetches it" % exc)
                os.remove(os.path.join(dest, name))
                failed.append(name)
                continue
            if args.delete_archives:
                os.remove(os.path.join(dest, name))
                print("  removed %s" % name)

    print("\n" + "=" * 60)
    if failed:
        print("incomplete: %s" % ", ".join(failed))
        print("re-run the same command; finished files are skipped and partial ones resume.")
    else:
        print("all %d archives downloaded." % len(wanted))
    if args.extract:
        n = count_images(dest)
        print("PNG files under %s: %d (expected %d)" % (dest, n, EXPECTED_IMAGES))
        if n != EXPECTED_IMAGES and len(wanted) == len(LINKS):
            print("count mismatch - re-run with --extract to refetch whatever is missing.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
