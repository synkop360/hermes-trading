"""Observed logical state budget and recovery headroom; no filesystem quota."""
import os,shutil,stat
from pathlib import Path
from hermes_trading.paired_packet_storage import MAX_PACKET_BYTES


class StorageStopReached(RuntimeError):
    pass


def validate_storage_limits(limits,experiment_id):
    if not isinstance(limits,dict) or type(limits.get('schema_version')) is not int or limits['schema_version']!=1 or limits.get('experiment_id')!=experiment_id:
        raise ValueError('storage limits require the current experiment identity')
    for key in ('max_state_bytes','reserve_bytes'):
        if type(limits.get(key)) is not int or not 0<limits[key]<=1024**4:raise ValueError('storage limits require finite positive integer bytes')
    if not 2*MAX_PACKET_BYTES<=limits['reserve_bytes']<limits['max_state_bytes']:
        raise ValueError('storage reserve must cover two maximum packets within the state budget')
    if 'protective_stop_on_pressure' in limits and type(limits['protective_stop_on_pressure']) is not bool:
        raise ValueError('protective storage opt-in must be a literal boolean')
    return limits


def _reject_link(info):
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
        raise ValueError('linked experiment storage is not admitted')


def inspect_storage(root,limits):
    validate_storage_limits(limits,limits.get('experiment_id'))
    report={'status':'blocked','reason':None,'state_bytes':None,'free_bytes':None,'limits':limits}
    try:
        root=Path(root);_reject_link(root.lstat())
        stack=[root];size=0;files=0
        while stack:
            directory=stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    info=entry.stat(follow_symlinks=False);_reject_link(info)
                    if stat.S_ISDIR(info.st_mode):stack.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):size+=info.st_size;files+=1
                    else:raise ValueError('nonregular experiment storage is not admitted')
                    if files>100000:raise ValueError('experiment file count exceeds admission bound')
        free=shutil.disk_usage(root).free
        if type(free) is not int or free<0:raise ValueError('filesystem free bytes are unavailable')
        report.update(state_bytes=size,free_bytes=free,files=files)
        if size>=limits['max_state_bytes']-limits['reserve_bytes']:report['reason']='state_budget'
        elif free<limits['reserve_bytes']:report['reason']='filesystem_reserve'
        else:report.update(status='ready',reason=None)
    except (OSError,ValueError) as error:report['reason']='measurement_unavailable:'+type(error).__name__
    return report
