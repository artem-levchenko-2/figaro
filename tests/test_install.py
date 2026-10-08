"""tools/install.sh and the skill it links.

install.sh runs here against temporary folders (--bin, --skills), never
against ~/.local/bin or ~/.claude/skills.

    pytest tests/test_install.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import figaro

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "install.sh"
SKILL = ROOT / "skill" / "figaro"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="install.sh is for macOS / Linux")


def install(tmp_path, *extra):
    bin_dir, skills = tmp_path / "bin", tmp_path / "skills"
    r = subprocess.run(["bash", str(SCRIPT), "--bin", str(bin_dir), "--skills", str(skills), *extra],
                       capture_output=True, text=True, timeout=120)
    return r, bin_dir / "figaro", skills / "figaro"


def test_installs_a_command_that_runs_from_any_folder(tmp_path):
    r, cmd, link = install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.access(cmd, os.X_OK)
    assert str(ROOT / "venv" / "bin" / "python") in cmd.read_text()
    run = subprocess.run([str(cmd), "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and "inspect" in run.stdout
    assert link.is_symlink() and link.resolve() == SKILL
    assert (link / "SKILL.md").exists()


def test_running_it_again_changes_nothing(tmp_path):
    _, cmd, _ = install(tmp_path)
    before = cmd.read_text()
    r, cmd, _ = install(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "already there" in r.stdout and cmd.read_text() == before


def test_a_stale_command_of_ours_is_rewritten(tmp_path):
    _, cmd, _ = install(tmp_path)
    cmd.write_text(cmd.read_text().replace("figaro.py", "old.py"))
    r, cmd, _ = install(tmp_path)
    assert r.returncode == 0 and "figaro.py" in cmd.read_text() and "old.py" not in cmd.read_text()


def test_never_overwrites_what_is_not_ours(tmp_path):
    (tmp_path / "bin").mkdir()
    foreign = tmp_path / "bin" / "figaro"
    foreign.write_text("#!/bin/sh\necho someone else\n")
    r, _, link = install(tmp_path)
    assert r.returncode == 1 and "not ours" in r.stderr
    assert foreign.read_text() == "#!/bin/sh\necho someone else\n"

    foreign.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.mkdir()
    r, _, _ = install(tmp_path)
    assert r.returncode == 1 and "not a link" in r.stderr and link.is_dir() and not link.is_symlink()


def test_uninstall_removes_only_ours(tmp_path):
    install(tmp_path)
    r, cmd, link = install(tmp_path, "--uninstall")
    assert r.returncode == 0 and not cmd.exists() and not link.is_symlink()

    cmd.write_text("mine")
    r, cmd, _ = install(tmp_path, "--uninstall")
    assert "not ours" in r.stdout and cmd.read_text() == "mine"


# ─── the skill ────────────────────────────────────────────────────────────

def frontmatter():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md starts with a --- frontmatter block"
    head = m.group(1)
    name = re.search(r"^name: (.+)$", head, re.M).group(1).strip()
    desc = re.search(r"^description: >-\n((?:  .*\n?)+)", head + "\n", re.M).group(1)
    return name, " ".join(line.strip() for line in desc.splitlines()), text


def test_skill_frontmatter():
    name, desc, _ = frontmatter()
    assert name == "figaro" == SKILL.name
    assert 0 < len(desc) <= 1024, len(desc)


def test_skill_names_every_command_and_reference():
    _, _, text = frontmatter()
    every = text + "".join(p.read_text(encoding="utf-8") for p in (SKILL / "references").glob("*.md"))
    for cmd in figaro.KNOWN_CMDS - {"import-component"}:  # icomp is its short name
        assert f"figaro {cmd}" in every or f"`{cmd}`" in every, cmd
    for ref in re.findall(r"`references/([\w-]+\.md)`", text):
        assert (SKILL / "references" / ref).exists(), ref
