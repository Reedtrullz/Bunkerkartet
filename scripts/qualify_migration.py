#!/usr/bin/env python3
"""Stage and qualify a supported schema migration without changing the rollback pair."""
import argparse,hashlib,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.db import Database,CURRENT_SCHEMA_VERSION
from scripts.restore_database import restore_archive
from scripts.backup_database import verify_backup
from scripts.verify_database import verify


def qualify_migration(archive,destination,*,source_version,candidate_data_volume,rollback_data_volume):
    if not candidate_data_volume or candidate_data_volume==rollback_data_volume:raise ValueError('migration candidate and rollback volumes must differ')
    original=hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    receipt=verify_backup(archive,expected_schema_version=source_version)
    restore_archive(archive,destination,expected_version=source_version)
    Database(Path(destination)/'bunkerkartet.sqlite3').initialize()
    verify(Path(destination)/'bunkerkartet.sqlite3',CURRENT_SCHEMA_VERSION)
    if hashlib.sha256(Path(archive).read_bytes()).hexdigest()!=original:raise RuntimeError('rollback archive changed')
    staged_database=Path(destination)/'bunkerkartet.sqlite3'
    result={'database_sha256':hashlib.sha256(staged_database.read_bytes()).hexdigest(),'database_size_bytes':staged_database.stat().st_size,'verified':True,'staged_only':True,'schema_before':source_version,'schema_after':CURRENT_SCHEMA_VERSION,'backup_sha256':receipt['sha256'],'candidate_data_volume':candidate_data_volume,'rollback_data_volume':rollback_data_volume,'startup_readiness':'requires separate exact-image evidence','qualification':'database integrity, schema, and preserved rollback archive'}
    target=Path(destination)/'migration-qualification.json'
    with target.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
    os.chmod(target,0o600)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True);parser.add_argument('--source-version',type=int,required=True)
    parser.add_argument('--candidate-data-volume',required=True);parser.add_argument('--rollback-data-volume',required=True)
    args=parser.parse_args()
    try:result=qualify_migration(args.archive,args.destination,source_version=args.source_version,candidate_data_volume=args.candidate_data_volume,rollback_data_volume=args.rollback_data_volume)
    except (OSError,RuntimeError,ValueError):print('migration qualification failed; rollback archive retained',file=sys.stderr);raise SystemExit(1)
    print(json.dumps(result,sort_keys=True))
