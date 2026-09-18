"""Exercise closed peers through the rendered native host, not system Python.

Run with Briefcase installed and --python-framework /path/to/Python.framework.
The framework is read-only; all generated apps live in a temporary directory.
"""

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile

from cookiecutter.main import cookiecutter


PROBE = '''import os, signal, socket
for kind in ('pipe', 'socket'):
    if kind == 'pipe':
        reader, writer = os.pipe()
        os.close(reader)
    else:
        peer, local = socket.socketpair()
        peer.close()
        writer = local.detach()
    try:
        try:
            os.write(writer, b'closed peer')
        except BrokenPipeError:
            print(kind + ':broken_pipe', flush=True)
        else:
            raise AssertionError('write to closed peer succeeded')
    finally:
        os.close(writer)
        print(kind + ':cleanup', flush=True)
assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN
# The embedding host must not change unrelated application signal dispositions.
assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
print('completed', flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-framework', type=Path, required=True)
    args = parser.parse_args()
    framework = args.python_framework.resolve()
    version = (framework / 'Versions/Current').resolve().name
    assert version.startswith('3.'), framework
    template = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='template-sigpipe-') as temporary:
        work = Path(temporary)
        for console in (False, True):
            generated = Path(cookiecutter(
                str(template), no_input=True, output_dir=str(work),
                extra_context={
                    'format': 'Console' if console else 'GUI',
                    'formal_name': 'Probe', 'app_name': 'probe',
                    'python_version': version + '.0', 'console_app': console,
                },
            ))
            contents = work / ('console.app' if console else 'gui.app') / 'Contents'
            for part in ('MacOS', 'Frameworks', 'Resources/app', 'Resources/app_packages'):
                (contents / part).mkdir(parents=True, exist_ok=True)
            (contents / 'Frameworks/Python.framework').symlink_to(framework)
            (contents / 'Info.plist').write_bytes(plistlib.dumps({
                'CFBundleExecutable': 'Probe', 'CFBundlePackageType': 'APPL',
                'CFBundleIdentifier': 'org.beeware.sigpipe-probe',
                'MainModule': 'sigpipe_probe',
            }))
            (contents / 'Resources/app/sigpipe_probe.py').write_text(PROBE)
            executable = contents / 'MacOS/Probe'
            subprocess.run([
                'clang', str(generated / 'Probe/main.m'),
                '-F' + str(framework.parent), '-framework', 'Python',
                '-framework', 'Cocoa', '-Wl,-rpath,' + str(framework.parent),
                '-o', str(executable),
            ], check=True, capture_output=True)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('PYTHON', 'BRIEFCASE'))}
            # Suppress crash dialogs in the negative (unpatched) control.
            env['BRIEFCASE_MAIN_MODULE'] = 'sigpipe_probe'
            result = subprocess.run(
                [str(executable)], env=env, capture_output=True,
                text=True, timeout=20, restore_signals=True,
            )
            assert result.returncode == 0, (console, result.returncode, result.stderr)
            assert result.stdout.splitlines() == [
                'pipe:broken_pipe', 'pipe:cleanup',
                'socket:broken_pipe', 'socket:cleanup', 'completed',
            ], result.stdout
            print(('Console' if console else 'GUI') + ': closed peers preserve cleanup')


if __name__ == '__main__':
    main()
