"""tools/install.ps1, on Windows, in Windows PowerShell 5.1 and in PowerShell 7.

It runs here against temporary folders (-Bin, -Skills), so it never touches the
user's PATH, %USERPROFILE%\\.local\\bin or the skill folders. It does use this
checkout's venv, where pip makes figaro.exe.

    pytest tests/test_install_ps1.py
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "install.ps1"
SKILL = ROOT / "skill" / "figaro"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 is for Windows")


def run(*args, shell="powershell"):
    return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                           *map(str, args)], capture_output=True, text=True, timeout=600)


def install(tmp_path, *extra, shell="powershell"):
    bin_dir, skills = tmp_path / "bin", tmp_path / "skills"
    r = run("-Bin", bin_dir, "-Skills", skills, *extra, shell=shell)
    return r, bin_dir / "figaro.exe", skills / "figaro"


def is_skill_link(path):
    return path.is_dir() and Path(os.path.realpath(path)) == Path(os.path.realpath(SKILL))


def test_installs_a_command_and_a_skill_link(tmp_path):
    r, cmd, link = install(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"command: {cmd}" in r.stdout and f"skill: {link} -> {SKILL}" in r.stdout
    assert is_skill_link(link) and (link / "SKILL.md").exists()
    assert "open a new terminal" in r.stdout  # -Bin is not put on PATH
    help_ = subprocess.run([str(cmd), "--help"], capture_output=True, text=True, timeout=60,
                           cwd=tmp_path)
    assert help_.returncode == 0 and "usage: figaro" in help_.stdout, help_.stderr


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell 7 is not installed")
def test_a_second_run_changes_nothing_in_either_powershell(tmp_path):
    assert install(tmp_path)[0].returncode == 0
    for shell in ("powershell", "pwsh"):
        r, cmd, link = install(tmp_path, shell=shell)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"command: {cmd} (already there)" in r.stdout
        assert f"skill: {link} (already there)" in r.stdout


def test_skills_takes_several_folders_in_one_argument(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    r = run("-Bin", tmp_path / "bin", "-Skills", f"{a},{b}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert is_skill_link(a / "figaro") and is_skill_link(b / "figaro")


def test_never_overwrites_what_it_did_not_make(tmp_path):
    bin_dir, skills = tmp_path / "bin", tmp_path / "skills"
    bin_dir.mkdir()
    (bin_dir / "figaro.exe").write_bytes(b"someone else's")
    r = run("-Bin", bin_dir, "-Skills", skills)
    assert r.returncode == 1 and "not ours" in r.stderr
    assert (bin_dir / "figaro.exe").read_bytes() == b"someone else's"

    (bin_dir / "figaro.exe").unlink()
    (skills / "figaro").mkdir(parents=True)
    (skills / "figaro" / "mine.txt").write_text("keep")
    other = tmp_path / "other"
    r = run("-Bin", bin_dir, "-Skills", f"{skills},{other}")
    assert r.returncode == 1 and "is not a link to" in r.stderr
    assert (skills / "figaro" / "mine.txt").read_text() == "keep"
    assert is_skill_link(other / "figaro")  # the other folder still gets its link


def test_uninstall_removes_only_its_own(tmp_path):
    r, cmd, link = install(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    foreign = tmp_path / "foreign"
    (foreign / "figaro").mkdir(parents=True)
    r = run("-Bin", tmp_path / "bin", "-Skills", f"{tmp_path / 'skills'},{foreign}", "-Uninstall")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not cmd.exists() and not os.path.lexists(link)
    assert (SKILL / "SKILL.md").exists()  # the junction went, not what it pointed to
    assert (foreign / "figaro").is_dir() and "not ours" in r.stdout


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Recorder(http.server.BaseHTTPRequestHandler):
    bodies = []

    def do_POST(self):
        Recorder.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        body = json.dumps({"ok": True, "result": "done"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_figaro_exe_passes_an_ampersand_from_cmd(tmp_path):
    """Figma links carry `&t=`: a .cmd file would hand them to cmd.exe, which
    cuts them at the `&`. figaro.exe gets them whole, from cmd too."""
    r, cmd, _ = install(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    port = free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, FIGARO_PORT=str(port), FIGARO_AUTOSTART="0")
    link = "https://www.figma.com/design/AbCd1234/Shop?node-id=1-2&t=abc"
    # The way people type it in cmd: the code and the link in double quotes.
    typed = f'cmd /c {cmd} exec "return \'a&b\'" -T "{link}"'
    try:
        for argv in ([str(cmd), "exec", "return 'a&b'", "-T", link], typed):
            Recorder.bodies.clear()
            out = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60,
                                 cwd=tmp_path)
            assert out.returncode == 0, out.stdout + out.stderr
            assert Recorder.bodies[0]["code"] == "return 'a&b'"
            assert Recorder.bodies[0]["target"] == link
    finally:
        server.shutdown()
        server.server_close()
