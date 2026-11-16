"""Read embedded thumbnails from a bounded ZIP prefix, including streamed ZIPs.

Bambu's FTPS server does not always implement REST. Its print archives commonly
put the plate image first, with sizes only in ZIP data descriptors. Reading those
entries sequentially avoids transferring the following (potentially huge) G-code.
No files are extracted to disk and no printer contents are changed.
"""
import binascii
import struct
import time
import zlib

MAX_TRANSFER = 8 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
HEADER = struct.Struct('<4s5H3I2H')


def image_name(name):
    name = name.lower()
    import re
    return (name.startswith(('metadata/', 'auxiliaries/'))
            and name.endswith(('.png', '.jpg', '.jpeg'))
            and ('thumbnail' in name or re.fullmatch(r'metadata/plate_[0-9]+(?:_small)?\.png', name)))


class PrefixReader:
    def __init__(self, receive, limit=MAX_TRANSFER, timeout=30):
        self.receive = receive
        self.limit = limit
        self.deadline = time.monotonic() + timeout
        self.received = 0
        self.pending = b''

    def chunk(self):
        if time.monotonic() > self.deadline:
            raise ValueError('Zeitlimit beim Lesen des Vorschaubilds erreicht. Erneut im Leerlauf versuchen.')
        if self.pending:
            data, self.pending = self.pending, b''
            return data
        if self.received >= self.limit:
            raise ValueError('Im ersten 8-MiB-Archivabschnitt wurde kein lesbares Vorschaubild gefunden.')
        data = self.receive(min(65536, self.limit - self.received))
        if not data:
            raise ValueError('Druckarchiv unvollständig oder kein unterstütztes Vorschaubild vorhanden.')
        self.received += len(data)
        return data

    def read(self, size):
        if size < 0 or size > self.limit:
            raise ValueError('Ungültige Größe im Druckarchiv.')
        parts = bytearray()
        while len(parts) < size:
            chunk = self.chunk()
            take = min(size-len(parts), len(chunk))
            parts.extend(chunk[:take])
            self.unread(chunk[take:])
        return bytes(parts)

    def unread(self, data):
        self.pending = data + self.pending


def streamed_thumbnail(receive, *, limit=MAX_TRANSFER, timeout=30):
    reader = PrefixReader(receive, limit, timeout)
    expanded = 0
    for _ in range(1000):
        signature = reader.read(4)
        if signature in (b'PK\x01\x02', b'PK\x05\x06'):
            raise ValueError('Die Datei enthält kein unterstütztes eingebettetes Vorschaubild.')
        if signature != b'PK\x03\x04':
            raise ValueError('Das Druckarchiv enthält einen nicht unterstützten ZIP-Eintrag.')
        _, _, flags, method, _, _, expected_crc, compressed, size, namesize, extrasize = HEADER.unpack(signature + reader.read(26))
        name = reader.read(namesize).decode('utf-8' if flags & 0x800 else 'cp437', errors='replace')
        extra = reader.read(extrasize)
        zip64 = size == 0xffffffff or compressed == 0xffffffff
        if zip64:
            remaining = extra
            found = False
            while len(remaining) >= 4:
                tag, length = struct.unpack('<HH', remaining[:4])
                value, remaining = remaining[4:4+length], remaining[4+length:]
                if tag == 1:
                    try:
                        if size == 0xffffffff:size, value = struct.unpack('<Q', value[:8])[0], value[8:]
                        if compressed == 0xffffffff:compressed = struct.unpack('<Q', value[:8])[0]
                    except struct.error:
                        raise ValueError('Ungültige ZIP64-Größen im Druckarchiv.')
                    found = True
                    break
            if not found:raise ValueError('ZIP64-Größen fehlen im Druckarchiv.')
        if flags & (1 | 64) or method not in (0, 8):
            raise ValueError('Verschlüsseltes oder nicht unterstütztes Druckarchiv.')
        candidate = bool(image_name(name))
        image = bytearray()
        crc = actual_size = actual_compressed = 0

        def consume(data):
            nonlocal crc, actual_size, expanded
            actual_size += len(data)
            expanded += len(data)
            if expanded > MAX_EXPANDED or candidate and actual_size > MAX_IMAGE:
                raise ValueError('Vorschaubild oder entpackte Archivdaten überschreiten die Größenbegrenzung.')
            crc = binascii.crc32(data, crc)
            if candidate:image.extend(data)

        descriptor_read = False
        if method == 8:
            decoder = zlib.decompressobj(-15)
            while not decoder.eof:
                chunk = reader.chunk()
                actual_compressed += len(chunk)
                while True:
                    consume(decoder.decompress(chunk, 65536))
                    if decoder.eof:
                        reader.unread(decoder.unused_data)
                        actual_compressed -= len(decoder.unused_data)
                        break
                    chunk = decoder.unconsumed_tail
                    if not chunk:break
        elif flags & 8:
            # Stored entries with unknown length need a validated data descriptor.
            # A coincidental PK marker in image pixels is not accepted as an end.
            pending = b''
            descriptor_size = 24 if zip64 else 16
            while not descriptor_read:
                pending += reader.chunk()
                offset = 0
                while True:
                    at = pending.find(b'PK\x07\x08', offset)
                    if at < 0 or len(pending)-at < descriptor_size:break
                    desc = struct.unpack('<IQQ' if zip64 else '<III', pending[at+4:at+descriptor_size])
                    length = actual_size + at
                    if desc == (binascii.crc32(pending[:at], crc), length, length):
                        consume(pending[:at]);actual_compressed = actual_size
                        reader.unread(pending[at+descriptor_size:])
                        descriptor_read = True
                        break
                    offset = at + 1
                if not descriptor_read:
                    keep = descriptor_size-1
                    if len(pending) > keep:
                        consume(pending[:-keep]);pending = pending[-keep:]
        else:
            if compressed != size:raise ValueError('Ungültige ZIP-Größen im Druckarchiv.')
            remaining = size
            while remaining:
                chunk = reader.read(min(65536, remaining));consume(chunk);remaining -= len(chunk)
            actual_compressed = actual_size
        if flags & 8 and not descriptor_read:
            prefix = reader.read(4)
            if prefix == b'PK\x07\x08':prefix = reader.read(4)
            desc = (struct.unpack('<I', prefix)[0], *struct.unpack('<QQ' if zip64 else '<II', reader.read(16 if zip64 else 8)))
            if desc != (crc, actual_compressed, actual_size):raise ValueError('Prüfsumme oder Größe des ZIP-Eintrags stimmt nicht.')
        elif not flags & 8 and (crc, actual_compressed, actual_size) != (expected_crc, compressed, size):
            raise ValueError('Prüfsumme oder Größe des ZIP-Eintrags stimmt nicht.')
        if candidate:
            if image.startswith(b'\x89PNG\r\n\x1a\n'):return bytes(image), 'image/png'
            if image.startswith(b'\xff\xd8\xff'):return bytes(image), 'image/jpeg'
    raise ValueError('Zu viele Einträge vor dem Vorschaubild im Druckarchiv.')
