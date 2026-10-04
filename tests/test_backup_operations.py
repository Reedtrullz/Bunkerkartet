import json
import sqlite3
import subprocess
from pathlib import Path
import pytest
from scripts.backup_operations import live_source, status, acknowledge, encrypt_snapshot, decrypt_and_verify, digest, MAX_CIPHER_BYTES


def test_source_rejects_mutable_image_or_ambiguous_data_volume():
    inspect={'Config':{'Image':'ghcr.io/reedtrullz/bunkerkartet:latest','Env':['APP_VERSION='+'a'*40]},'Mounts':[{'Destination':'/app/data','Type':'volume','Name':'data'}]}
    with pytest.raises(ValueError):live_source(inspect)
    inspect['Config']['Image']='ghcr.io/reedtrullz/bunkerkartet@sha256:'+'b'*64
    assert live_source(inspect)==(inspect['Config']['Image'],'a'*40,'data')
    inspect['Mounts'].append(inspect['Mounts'][0])
    with pytest.raises(ValueError):live_source(inspect)


def test_missing_stale_failure_and_offsite_lag_are_distinct(tmp_path):
    assert status(tmp_path,now=100000)['problems']==['missing-local-backup','missing-offhost-copy']
    (tmp_path/'latest.json').write_text(json.dumps({'created_at_epoch':5000,'stamp':'20260101T000000Z','cipher_sha256':'a'*64}))
    assert 'stale-local-backup' in status(tmp_path,now=100000)['problems']
    (tmp_path/'offhost.json').write_text(json.dumps({'created_at_epoch':100000,'stamp':'20260101T000000Z','cipher_sha256':'a'*64}))
    (tmp_path/'failure.json').write_text(json.dumps({'at_epoch':100001}))
    assert 'newer-backup-failure' in status(tmp_path,now=100002)['problems']
    (tmp_path/'latest.json').write_text(json.dumps({'created_at_epoch':100000,'stamp':'20260101T000000Z','cipher_sha256':'b'*64}))
    assert 'offhost-copy-behind' in status(tmp_path,now=100002)['problems']


def test_acknowledgement_refuses_other_bytes_and_does_not_advance_on_failure(tmp_path):
    folder=tmp_path/'20260101T000000Z';folder.mkdir()
    cipher=folder/'archive.tar.gz.cms';cipher.write_bytes(b'encrypted')
    (folder/'receipt.json').write_text(json.dumps({'stamp':folder.name,'created_at_epoch':1,'cipher_sha256':digest(cipher),'cipher_size':cipher.stat().st_size}))
    with pytest.raises(ValueError):acknowledge(tmp_path,folder.name,'0'*64)
    assert not (tmp_path/'offhost.json').exists()
    with pytest.raises(ValueError):acknowledge(tmp_path,folder.name,digest(cipher))
    assert not (tmp_path/'offhost.json').exists()
    with pytest.raises(ValueError):acknowledge(tmp_path,'../../elsewhere',digest(cipher))


def test_fresh_receipts_cannot_hide_missing_or_corrupt_local_cipher(tmp_path):
    value={'created_at_epoch':100000,'stamp':'20260101T000000Z','cipher_sha256':'a'*64}
    for name in ['latest.json','offhost.json']:(tmp_path/name).write_text(json.dumps(value))
    assert 'local-cipher-missing-or-corrupt' in status(tmp_path,now=100001)['problems']


def test_real_encryption_restore_detects_corruption_and_wrong_key(tmp_path):
    cert=tmp_path/'recipient.pem';key=tmp_path/'private.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=test','-keyout',str(key),'-out',str(cert)],capture_output=True,check=True)
    database=tmp_path/'bunkerkartet.sqlite3'
    with sqlite3.connect(database) as c:
        c.execute('PRAGMA user_version=16');c.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT)');c.execute('INSERT INTO sample(value) VALUES (?)',('private-canary',))
    import tarfile
    archive=tmp_path/'archive.tar.gz'
    with tarfile.open(archive,'w:gz') as t:t.add(database,arcname='bunkerkartet.sqlite3')
    receipt={'database_schema_version':16,'sha256':digest(archive),'archive':'archive.tar.gz','verified':True}
    sealed=tmp_path/'sealed';sealed.mkdir()
    metadata=encrypt_snapshot(archive,receipt,cert,sealed,stamp='20260101T000000Z',created_at=1)
    cipher=sealed/'archive.tar.gz.cms'
    assert b'private-canary' not in cipher.read_bytes()
    summary=decrypt_and_verify(cipher,metadata,key,cert,tmp_path)
    assert summary['tables']['sample']['count']==1
    from scripts.backup_operations import attest, validate_attestation
    proof=attest(metadata,summary,key,tmp_path)
    validate_attestation(proof,metadata,cert,tmp_path)
    ack_folder=tmp_path/metadata['stamp'];ack_folder.mkdir()
    (ack_folder/'archive.tar.gz.cms').write_bytes(cipher.read_bytes())
    (ack_folder/'receipt.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError):acknowledge(tmp_path,metadata['stamp'],metadata['cipher_sha256'])
    acknowledge(tmp_path,metadata['stamp'],metadata['cipher_sha256'],proof=proof,certificate=cert)
    assert status(tmp_path,now=2,certificate=cert)['offhost']['decrypted_rows_verified']
    bad=json.loads(json.dumps(proof));bad['payload']['database_proof_sha256']='0'*64
    with pytest.raises(ValueError):validate_attestation(bad,metadata,cert,tmp_path)
    bad=json.loads(json.dumps(proof));bad['signature']=bad['signature'][::-1]
    with pytest.raises(ValueError):validate_attestation(bad,metadata,cert,tmp_path)
    assert not list(tmp_path.glob('.decrypted-*'))
    saved=cipher.read_bytes();cipher.write_bytes(saved[:-1]+bytes([saved[-1]^1]))
    with pytest.raises(ValueError):decrypt_and_verify(cipher,metadata,key,cert,tmp_path)
    cipher.write_bytes(saved)
    wrong=tmp_path/'wrong.pem';wrongcert=tmp_path/'wrong-cert.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=wrong','-keyout',str(wrong),'-out',str(wrongcert)],capture_output=True,check=True)
    with pytest.raises(ValueError):decrypt_and_verify(cipher,metadata,wrong,wrongcert,tmp_path)
    assert not list(tmp_path.glob('.decrypted-*'))


def test_encrypted_download_is_bounded_even_if_remote_sends_more(tmp_path):
    import io
    from scripts.pull_encrypted_backup import receive
    output=io.BytesIO()
    with pytest.raises(ValueError):receive(io.BytesIO(b'123456789'),output,3)
    assert len(output.getvalue())<=3
    with pytest.raises(ValueError):receive(io.BytesIO(b'12'),io.BytesIO(),3)
    valid=io.BytesIO();receive(io.BytesIO(b'123'),valid,3);assert valid.getvalue()==b'123'


def test_hash_only_acknowledgement_cannot_assert_decryption(tmp_path):
    folder=tmp_path/'20260101T000000Z';folder.mkdir()
    cipher=folder/'archive.tar.gz.cms';cipher.write_bytes(b'not-a-decrypted-database')
    (folder/'receipt.json').write_text(json.dumps({'stamp':folder.name,'created_at_epoch':1,'cipher_sha256':digest(cipher),'cipher_size':cipher.stat().st_size}))
    with pytest.raises(ValueError):acknowledge(tmp_path,folder.name,digest(cipher))
    assert not (tmp_path/'offhost.json').exists()


def test_30_days_twice_daily_capacity_includes_receipts_and_next_snapshot():
    from scripts.backup_operations import MAX_STORAGE_BYTES
    assert 60*(MAX_CIPHER_BYTES+64*1024)+MAX_CIPHER_BYTES <= MAX_STORAGE_BYTES


def test_receipt_is_flushed_to_disk_before_return(tmp_path,monkeypatch):
    from scripts import backup_operations as module
    calls=[]
    real=module.os.fsync
    def record(fd):
        calls.append(fd);real(fd)
    monkeypatch.setattr(module.os,'fsync',record)
    module.save(tmp_path/'receipt.json',{'verified':True})
    assert len(calls)>=2


def test_receiver_flushes_cipher_receipt_and_directories_before_ack(tmp_path,monkeypatch):
    import os,time,tarfile
    from scripts import pull_encrypted_backup as receiver
    cert=tmp_path/'recipient.pem';key=tmp_path/'private.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=test','-keyout',str(key),'-out',str(cert)],capture_output=True,check=True)
    database=tmp_path/'db.sqlite3'
    with sqlite3.connect(database) as c:c.execute('PRAGMA user_version=16');c.execute('CREATE TABLE sample(id INTEGER)')
    archive=tmp_path/'archive.tar.gz'
    with tarfile.open(archive,'w:gz') as t:t.add(database,arcname='bunkerkartet.sqlite3')
    root=tmp_path/'copies';root.mkdir();folder=root/'20260101T000000Z';folder.mkdir()
    metadata=encrypt_snapshot(archive,{'database_schema_version':16,'sha256':digest(archive),'archive':'archive.tar.gz','verified':True},cert,folder,stamp=folder.name,created_at=time.time())
    synced=[];actual=os.fsync
    def sync(fd):
        synced.append(os.fstat(fd).st_ino);actual(fd)
    monkeypatch.setattr(os,'fsync',sync)
    acknowledged=[]
    def remote(command,content=None):
        if command.endswith(' latest'):return json.dumps(metadata).encode()
        assert 'acknowledge' in command and content
        for path in (folder/'archive.tar.gz.cms',folder/'receipt.json',folder,root):assert path.stat().st_ino in synced
        assert json.loads(content)['payload']['decrypted_rows_verified'] is True
        acknowledged.append(True)
        return json.dumps({'decrypted_rows_verified':True}).encode()
    monkeypatch.setattr(receiver,'remote',remote)
    assert receiver.pull({'directory':str(root),'private_key':str(key),'certificate':str(cert)})['verified']
    assert acknowledged==[True]
