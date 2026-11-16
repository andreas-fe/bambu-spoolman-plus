import io
import struct
import zipfile
import pytest
from bridge.archive_preview import streamed_thumbnail

PNG=b'\x89PNG\r\n\x1a\nexample PK\x07\x08 pixels'

class Unseekable(io.BytesIO):
    def seekable(self):return False
    def seek(self,*args):raise OSError('stream')


def archive(*,stream=False,method=zipfile.ZIP_STORED,zip64=False):
    f=Unseekable() if stream else io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:
        z.writestr('[Content_Types].xml','<Types/>',compress_type=zipfile.ZIP_DEFLATED)
        info=zipfile.ZipInfo('Metadata/plate_1.png');info.compress_type=method
        with z.open(info,'w',force_zip64=zip64) as out:out.write(PNG)
        z.writestr('Metadata/plate_1.gcode',b'X'*1024*1024)
    return f.getvalue()


@pytest.mark.parametrize('stream,method,zip64',[(False,0,False),(False,8,True),(True,0,False),(True,0,True),(True,8,False),(True,8,True)])
def test_reads_embedded_image_before_large_gcode(stream,method,zip64):
    data=archive(stream=stream,method=method,zip64=zip64);source=io.BytesIO(data)
    # Small and uneven network packets also split ZIP headers and descriptors.
    def receive(size):return source.read(min(size,19))
    assert streamed_thumbnail(receive)==(PNG,'image/png')
    assert source.tell()<1024  # No full archive or G-code transfer.


def test_corruption_truncation_bounds_and_missing_image():
    data=archive();changed=bytearray(data);offset=data.index(PNG);changed[offset+9]^=1
    with pytest.raises(ValueError,match='Prüfsumme'):streamed_thumbnail(io.BytesIO(changed).read)
    with pytest.raises(ValueError):streamed_thumbnail(io.BytesIO(data[:offset+5]).read)
    with pytest.raises(ValueError):streamed_thumbnail(io.BytesIO(data).read,limit=30)
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:z.writestr('Metadata/plate_1.gcode','G1 X0')
    with pytest.raises(ValueError,match='kein unterstütztes'):streamed_thumbnail(io.BytesIO(f.getvalue()).read)


def test_timeout_and_decompression_limit(monkeypatch):
    import bridge.archive_preview as m
    clock=iter([0,2]);monkeypatch.setattr(m.time,'monotonic',lambda:next(clock))
    with pytest.raises(ValueError,match='Zeitlimit'):streamed_thumbnail(io.BytesIO(archive()).read,timeout=1)
    monkeypatch.undo();monkeypatch.setattr(m,'MAX_EXPANDED',100)
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\n'+b'X'*1000,compress_type=8)
    with pytest.raises(ValueError,match='Größenbegrenzung'):streamed_thumbnail(io.BytesIO(f.getvalue()).read)


def test_deflated_unsigned_descriptor():
    data=archive(stream=True,method=8)
    data=data.replace(b'PK\x07\x08',b'')
    # CRC and sizes remain authoritative; ZIP central offsets are not required.
    assert streamed_thumbnail(io.BytesIO(data).read)==(PNG,'image/png')


def test_bad_descriptor_is_not_accepted_as_valid_image():
    data=bytearray(archive(stream=True));descriptor=data.index(b'PK\x07\x08',data.index(PNG)+len(PNG))
    data[descriptor+4]^=1
    with pytest.raises(ValueError):streamed_thumbnail(io.BytesIO(data).read)


def test_oversized_stored_image_is_rejected(monkeypatch):
    import bridge.archive_preview as m
    monkeypatch.setattr(m,'MAX_IMAGE',10)
    with pytest.raises(ValueError,match='Größenbegrenzung'):streamed_thumbnail(io.BytesIO(archive(stream=True)).read)


def test_stored_descriptor_split_at_every_boundary():
    data=archive(stream=True);descriptor=data.index(b'PK\x07\x08',data.index(PNG)+len(PNG))
    for offset in range(17):
        source=io.BytesIO(data);first=[True]
        def receive(size):
            if first[0]:first[0]=False;return source.read(min(size,descriptor+offset))
            return source.read(min(size,7))
        assert streamed_thumbnail(receive)==(PNG,'image/png')
