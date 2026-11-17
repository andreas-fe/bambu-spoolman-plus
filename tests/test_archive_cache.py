import io
import time
from pathlib import Path
import pytest
from bridge.archive_cache import ArchiveCache


def test_capacity_identity_and_open_reader_survive_eviction():
    cache=ArchiveCache(max_bytes=6,max_entries=2)
    cache.put('old',io.BytesIO(b'1234'));reader=cache.get('old')
    cache.put('other',io.BytesIO(b'5678'))
    assert cache.get('old') is None and reader.read()==b'1234';reader.close()
    with cache.get('other') as result:assert result.read()==b'5678'
    cache.put('oversize',io.BytesIO(b'1234567'));assert cache.get('oversize') is None
    cache.clear();assert not list(Path(cache.directory.name).iterdir())


def test_expiry_removes_files_without_another_request():
    cache=ArchiveCache(ttl=.03);cache.put('key',io.BytesIO(b'archive'))
    until=time.monotonic()+1
    while list(Path(cache.directory.name).iterdir()) and time.monotonic()<until:time.sleep(.01)
    assert not list(Path(cache.directory.name).iterdir()) and cache.get('key') is None


def test_clear_during_copy_and_cancellation_do_not_repopulate_cache():
    cache=ArchiveCache();generation=cache.generation;first=[True]
    def clear():
        if first[0]:first[0]=False;cache.clear()
    cache.put('old',io.BytesIO(b'x'*2000000),clear,generation)
    assert cache.get('old') is None and not list(Path(cache.directory.name).iterdir())
    def cancel():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError):cache.put('partial',io.BytesIO(b'archive'),cancel)
    assert cache.get('partial') is None and not list(Path(cache.directory.name).iterdir())
