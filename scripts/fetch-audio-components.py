"""Download pinned public audio components with licenses, integrity records, no install scripts."""
import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RN = 'cb529a59a8478fe604e57986fc96afdaecfa6fb7'
RN_CORE = '372f7b4b76cde4ca1ec4605353dd17898a99de38'
SILERO = '066409f3d9db45a30d21dfcbf9147826ffdd7052'
ORT = '1.22.0'
records = []

def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()

def save(path, data, url, license_id):
    destination = ROOT / path
    digest = hashlib.sha256(data).hexdigest()
    if destination.exists() and destination.read_bytes() != data:
        raise RuntimeError('Refusing to overwrite a different asset: ' + path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    records.append(dict(path=path, source=url, sha256=digest, bytes=len(data), license=license_id))
    print(path, len(data), flush=True)

def github(repo, ref, source, target, license_id, blob_sha=None):
    url = f'https://raw.githubusercontent.com/{repo}/{ref}/{source}'
    data = download(url)
    if blob_sha:
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == blob_sha
    save(target, data, url, license_id)

if __name__ == '__main__':
    github('jitsi/rnnoise-wasm', RN, 'LICENSE', 'compliance/licenses/audio/jitsi-LICENSE.txt', 'Apache-2.0')
    github('xiph/rnnoise', RN_CORE, 'COPYING', 'compliance/licenses/audio/rnnoise-COPYING.txt', 'BSD-3-Clause')
    github('snakers4/silero-vad', SILERO, 'LICENSE', 'compliance/licenses/audio/silero-LICENSE.txt', 'MIT')
    github('jitsi/rnnoise-wasm', RN, 'dist/rnnoise-sync.js', 'web/vendor/audio/rnnoise-sync.js',
           'Apache-2.0 AND BSD-3-Clause', 'd054e0640b918141ee80f7b6e2eb5bcac9fe38a4')
    github('snakers4/silero-vad', SILERO, 'src/silero_vad/data/silero_vad.onnx',
           'web/vendor/audio/silero_vad.onnx', 'MIT', '80c5592ef1f4c9ede3e357bbd02eb863358a6a9d')
    metadata = json.loads(download(f'https://registry.npmjs.org/onnxruntime-web/{ORT}'))
    url = metadata['dist']['tarball']
    assert url == f'https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-{ORT}.tgz'
    archive = download(url)
    integrity = 'sha512-' + base64.b64encode(hashlib.sha512(archive).digest()).decode()
    assert integrity == metadata['dist']['integrity'], 'npm archive integrity mismatch'
    files = {'package/dist/ort.wasm.min.js': 'web/vendor/audio/ort.wasm.min.js',
             'package/dist/ort-wasm-simd-threaded.mjs': 'web/vendor/audio/ort-wasm-simd-threaded.mjs',
             'package/dist/ort-wasm-simd-threaded.wasm': 'web/vendor/audio/ort-wasm-simd-threaded.wasm'}
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member, target in files.items():
            save(target, tar.extractfile(member).read(), url + '#' + member, 'MIT (plus bundled third-party notices)')
    github('microsoft/onnxruntime', 'v' + ORT, 'LICENSE', 'compliance/licenses/audio/onnxruntime-LICENSE.txt', 'MIT')
    github('microsoft/onnxruntime', 'v' + ORT, 'ThirdPartyNotices.txt',
           'compliance/licenses/audio/onnxruntime-ThirdPartyNotices.txt', 'Third-party notices')
    manifest = {'checked_on': '2026-11-04', 'rnnoise_wasm_commit': RN, 'rnnoise_core_commit': RN_CORE,
                'silero_commit': SILERO, 'onnxruntime_web_version': ORT, 'npm_integrity': integrity, 'files': records}
    (ROOT / 'compliance/audio-assets.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
