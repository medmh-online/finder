"""Profile-scoped, locally uploaded attachment store. Never accept Gemini-supplied paths."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .config import app_paths

MAX_BYTES = 12 * 1024 * 1024
TYPES = {
    'pdf': ('document', '.pdf'),
    'png': ('image', '.png'), 'jpeg': ('image', '.jpg'), 'jpg': ('image', '.jpg'),
    'webp': ('image', '.webp'),
    'ogg': ('voice', '.ogg'), 'opus': ('voice', '.ogg'),
    'mp3': ('voice', '.ogg'), 'wav': ('voice', '.ogg'),
    'm4a': ('voice', '.ogg'),
}


def media_root():
    root = app_paths().data / 'ai_media'
    if root.is_symlink():
        raise ValueError('Media directory must not be a symlink')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root.resolve()


def _check_magic(data, ext):
    if ext=='pdf': return data.startswith(b'%PDF-')
    if ext in ('png',): return data.startswith(b'\x89PNG\r\n\x1a\n')
    if ext in ('jpeg','jpg'): return data.startswith(b'\xff\xd8\xff')
    if ext=='webp': return data.startswith(b'RIFF') and data[8:12]==b'WEBP'
    if ext in ('opus','ogg'): return data.startswith(b'OggS')
    if ext=='mp3': return data.startswith(b'ID3') or (len(data)>1 and data[0]==0xff and data[1]&0xe0==0xe0)
    if ext=='wav': return data.startswith(b'RIFF') and data[8:12]==b'WAVE'
    if ext=='m4a': return data[4:8]==b'ftyp'
    return False


def store_upload(media_id, name, raw):
    """Store an uploaded file by opaque ID; only supported formats and bounded bytes."""
    if not isinstance(raw,bytes) or not raw or len(raw)>MAX_BYTES:
        raise ValueError('File must be between 1 byte and 12 MB')
    ext=Path(name or '').suffix.lower().lstrip('.')
    if ext not in TYPES or not _check_magic(raw, ext):
        raise ValueError('Unsupported or invalid file type')
    kind,target_ext=TYPES[ext]
    if len(media_id)!=32 or any(c not in '0123456789abcdef' for c in media_id):
        raise ValueError('Invalid media ID')
    root=media_root()
    payload=raw
    if kind=='voice' and not (ext in ('ogg','opus') and b'OpusHead' in raw[:4096]):
        # Original Ogg/Opus voice recordings work without extra software;
        # other formats are converted to WhatsApp-compatible Ogg/Opus.
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:
            raise ValueError('Install FFmpeg on PATH to convert MP3/M4A/WAV to Ogg/Opus voice notes')
        with tempfile.TemporaryDirectory(prefix='medmh-audio-') as temp:
            src=Path(temp)/('input.'+ext)
            dst=Path(temp)/'output.ogg'
            src.write_bytes(raw)
            try:
                subprocess.run([ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y',
                    '-i',str(src),'-vn','-ac','1','-ar','48000','-c:a','libopus',
                    '-b:a','32k','-t','180',str(dst)],check=True,
                    timeout=45,capture_output=True)
            except (subprocess.SubprocessError,OSError) as exc:
                raise ValueError('Could not convert audio to WhatsApp voice note') from exc
            payload=dst.read_bytes() if dst.exists() else b''
            if not payload.startswith(b'OggS') or len(payload)>MAX_BYTES:
                raise ValueError('Converted voice note is invalid or too large')
    filename=media_id+target_ext
    target=root/filename
    if target.exists() or target.is_symlink():
        raise ValueError('Media ID already exists')
    with target.open('xb') as f:
        f.write(payload)
    if os.name!='nt': target.chmod(0o600)
    return filename,kind,len(payload),hashlib.sha256(payload).hexdigest()


def remove_file(filename):
    # Only already-validated opaque file names can be unlinked.
    import re
    if not re.fullmatch(r'[0-9a-f]{32}\.(?:pdf|png|jpg|webp|ogg)',str(filename)):
        raise ValueError('Invalid media filename')
    target=media_root()/filename
    if target.is_symlink(): raise ValueError('Media symlink rejected')
    target.unlink(missing_ok=True)
