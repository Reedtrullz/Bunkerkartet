"""Read-only binding of a stopped candidate database to migration evidence."""
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_database import verify


def verify_candidate(path,*,expected_sha256,expected_size,expected_version):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < expected_size <= 512*1024*1024 or path.stat().st_size != expected_size:
        raise RuntimeError('candidate database size or file identity differs')
    for suffix in ('-wal','-journal'):
        residue=Path(str(path)+suffix)
        if residue.exists() and residue.stat().st_size:
            raise RuntimeError('candidate must be stopped and checkpointed without active journal residue')
    digest=hashlib.sha256()
    with path.open('rb') as source:
        while chunk:=source.read(1024*1024):digest.update(chunk)
    if digest.hexdigest()!=expected_sha256:raise RuntimeError('candidate database differs from qualified bytes')
    verify(path,expected_version)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--expected-size',type=int,required=True)
    parser.add_argument('--expected-version',type=int,required=True)
    args=parser.parse_args()
    try:verify_candidate(args.database,expected_sha256=args.expected_sha256,expected_size=args.expected_size,expected_version=args.expected_version)
    except (OSError,RuntimeError,ValueError):print('candidate volume qualification failed',file=sys.stderr);raise SystemExit(1)
