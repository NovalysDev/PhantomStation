"""Expose the build tool's actual progress while it is running."""
from pathlib import Path

MARKER = 'PHANTOM_REALTIME_BUILD_V1'

OLD = '''    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for stream in (proc.stdout, proc.stderr):
        if stream:
            for line in stream.splitlines():
                if line.strip():
                    progress.log(line)
    if proc.returncode != 0:
        err = f"cmake --build failed (exit {proc.returncode})"
        combined = "\\n".join(
            s for s in (proc.stdout or "", proc.stderr or "") if s
        )
        if "still dirty after" in combined:
'''

NEW = '''    # PHANTOM_REALTIME_BUILD_V1: forward Ninja's completed tasks immediately.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    dirty_loop = False
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\\r\\n")
        if line.strip():
            progress.log(line)
        if "still dirty after" in line:
            dirty_loop = True
    proc.stdout.close()
    returncode = proc.wait()
    if returncode != 0:
        err = f"cmake --build failed (exit {returncode})"
        if dirty_loop:
'''


def install_build_progress(project):
    target = Path(project) / 'psxrecomp' / 'psxrecomp_cli.py'
    source = target.read_text(encoding='utf-8')
    if MARKER in source:
        return
    start = source.find('def _cmake_build(')
    end = source.find('\n\nPGO_DEBUG_PORT', start)
    if start < 0 or end < 0:
        raise ValueError('La structure du compilateur a changé.')
    body = source[start:end]
    if body.count(OLD) != 1:
        raise ValueError('La sortie du compilateur ne peut pas être suivie en direct.')
    source = source[:start] + body.replace(OLD, NEW, 1) + source[end:]
    target.write_text(source, encoding='utf-8', newline='\n')
