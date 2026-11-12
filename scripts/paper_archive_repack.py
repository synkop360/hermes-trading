"""Losslessly recompress closed journal evidence; preserve raw-digest pins."""
import json,os,re,shutil,uuid,zipfile
from scripts import paper_cloud_install as install
from scripts import paper_release_transition as transition
from hermes_trading.reflection_supervision import _hash

PATTERN=r'(?:closed-journals|post-checkpoint-journal)-[0-9a-f]{32}\.zip'


class _NotSmaller(Exception):
    pass


def repack_archives(auxiliary,*,fault=None):
    auxiliary=transition._safe_root(auxiliary)
    if (transition._read(auxiliary/'owner.json').get('purpose')!='paper_cloud_auxiliary_v1' or
            transition._read(auxiliary/'installed.json').get('status')!='installed'):
        raise ValueError('completed owned installation required for repacking')
    receipt_path=transition._safe_root(auxiliary/'journal-repack.json')
    receipt=transition._read(receipt_path) if receipt_path.exists() else {'schema_version':1,'archives':{}}
    records=receipt.get('archives')
    if receipt.get('schema_version')!=1 or not isinstance(records,dict) or len(records)>64:
        raise ValueError('archive repack receipt differs')
    for name,record in records.items():
        if re.fullmatch(PATTERN,name) is None or not isinstance(record,dict):raise ValueError('archive repack identity differs')
        if any(not isinstance(record.get(k),str) or re.fullmatch('[0-9a-f]{64}',record[k]) is None
                for k in ('source_sha256','target_sha256','payload_hash')):
            raise ValueError('archive repack digest differs')
        path=transition._safe_root(auxiliary/name)
        if (install._digest_file(path) not in (record['source_sha256'],record['target_sha256']) or
                _hash(install._verify_evidence(path))!=record['payload_hash']):
            raise ValueError('retained repack evidence changed')
    metadata='\n'.join(json.dumps(transition._read(p),sort_keys=True)
        for p in auxiliary.glob('*.json') if p!=receipt_path)
    paths=sorted(list(auxiliary.glob('closed-journals-*.zip'))+list(auxiliary.glob('post-checkpoint-journal-*.zip')))
    if len(paths)>64:raise ValueError('archive repack count exceeds bound')
    reclaimed=0;changed=0
    for path in paths:
        transition._safe_root(path)
        if re.fullmatch(PATTERN,path.name) is None:raise ValueError('archive repack path differs')
        source_digest=install._digest_file(path);record=records.get(path.name)
        if record and source_digest==record['target_sha256']:continue
        # Existing audit and deduplication receipts may bind the ZIP bytes,
        # rather than its members. Never change those container identities.
        if source_digest in metadata:continue
        size=path.stat().st_size
        if size>64*1024*1024:raise ValueError('compressed source exceeds repack bound')
        if shutil.disk_usage(auxiliary).free<size+16*1024*1024:raise ValueError('repack temporary headroom required')
        hashes=install._verify_evidence(path)
        temporary=transition._safe_root(path.with_name(path.name+'.'+uuid.uuid4().hex+'.repack'))
        try:
            with zipfile.ZipFile(path) as source,zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_LZMA) as target:
                for info in source.infolist():
                    entry=zipfile.ZipInfo(info.filename,info.date_time)
                    entry.compress_type=zipfile.ZIP_LZMA;entry.external_attr=info.external_attr
                    with source.open(info) as reader,target.open(entry,'w') as writer:
                        while chunk:=reader.read(1024*1024):
                            writer.write(chunk)
                            if target.fp.tell()>size:raise _NotSmaller()
            if temporary.stat().st_size>=size:continue
            if install._verify_evidence(temporary)!=hashes:raise ValueError('repacked members differ')
            with temporary.open('r+b') as handle:os.fsync(handle.fileno())
            candidate=dict(source_sha256=source_digest,target_sha256=install._digest_file(temporary),payload_hash=_hash(hashes))
            if record and candidate!=record:raise ValueError('pending repack identity differs')
            records[path.name]=candidate;transition._save(receipt_path,receipt)
            if fault:fault()
            if install._digest_file(path)!=source_digest:raise ValueError('source changed during repack')
            new_size=temporary.stat().st_size
            os.replace(temporary,path);transition._sync_dir(auxiliary)
            reclaimed+=size-new_size;changed+=1
        except _NotSmaller:
            pass
        finally:
            if temporary.exists():transition._safe_root(temporary).unlink()
    return {'kind':'paper_journal_archive_repack','files_repacked':changed,'bytes_reclaimed':reclaimed}
