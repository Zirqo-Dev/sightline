"""Dry runs for the nft-data publish script. No GitHub calls."""
import base64
import os
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPT = os.path.join(ROOT, "nft", "loop", "publish_nft_data.sh")
TOKEN = "sekret-value-token"


def _env(extra=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C",
        "TOKEN": TOKEN,
        "NFT_PUSH_RETRY_SLEEP": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "nft-snapshot",
        "GIT_AUTHOR_EMAIL": "nft-snapshot@example.com",
        "GIT_COMMITTER_NAME": "nft-snapshot",
        "GIT_COMMITTER_EMAIL": "nft-snapshot@example.com",
    }
    if extra:
        env.update(extra)
    return env


def _run(cwd, extra=None):
    return subprocess.run(
        ["bash", SCRIPT], cwd=cwd, env=_env(extra), text=True, capture_output=True,
    )


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_pair():
    tmp = tempfile.mkdtemp()
    bare = os.path.join(tmp, "origin.git")
    data = os.path.join(tmp, "data")
    subprocess.run(["git", "init", "--bare", bare], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "nft-data", data], check=True, capture_output=True, text=True)
    _git(data, "remote", "add", "origin", bare)
    os.makedirs(os.path.join(data, "nft-pilot"))
    return tmp, bare, data


class PublishTests(unittest.TestCase):
    def test_first_run_creates_nft_data_and_later_run_fast_forwards(self):
        _tmp, bare, data = _init_pair()
        with open(os.path.join(data, "nft-pilot", "status.json"), "w") as fh:
            fh.write('{"status":"ok"}\n')
        first = _run(data)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn("fatal:", first.stderr)
        masked = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        self.assertIn("::add-mask::" + masked, first.stdout)
        self.assertNotIn(TOKEN, first.stdout + first.stderr)
        tip = subprocess.check_output(["git", "-C", bare, "rev-parse", "nft-data"], text=True).strip()
        shown = subprocess.check_output(["git", "-C", bare, "show", f"{tip}:nft-pilot/status.json"], text=True)
        self.assertIn('"status":"ok"', shown)
        branches = subprocess.check_output(["git", "-C", bare, "branch"], text=True)
        self.assertNotIn("desk-data", branches)

        again = _run(data)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("nothing to commit", again.stdout)
        same = subprocess.check_output(["git", "-C", bare, "rev-parse", "nft-data"], text=True).strip()
        self.assertEqual(same, tip)

        with open(os.path.join(data, "nft-pilot", "status.json"), "w") as fh:
            fh.write('{"status":"ok","n":2}\n')
        second = _run(data)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn(TOKEN, second.stdout + second.stderr)
        moved = subprocess.check_output(["git", "-C", bare, "rev-parse", "nft-data"], text=True).strip()
        self.assertNotEqual(moved, tip)
        parent = subprocess.check_output(["git", "-C", bare, "rev-parse", "nft-data^"], text=True).strip()
        self.assertEqual(parent, tip)

    def test_rejected_push_rebases_without_aborting_a_missing_rebase(self):
        _tmp, bare, data = _init_pair()
        with open(os.path.join(data, "nft-pilot", "a.txt"), "w") as fh:
            fh.write("a\n")
        first = _run(data)
        self.assertEqual(first.returncode, 0, first.stderr)
        subprocess.run(
            ["git", "-C", bare, "symbolic-ref", "HEAD", "refs/heads/nft-data"],
            check=True, capture_output=True, text=True,
        )
        other = os.path.join(_tmp, "other")
        subprocess.run(
            ["git", "clone", "-b", "nft-data", bare, other],
            check=True, capture_output=True, text=True,
        )
        with open(os.path.join(other, "nft-pilot", "b.txt"), "w") as fh:
            fh.write("b\n")
        _git(other, "add", "nft-pilot/b.txt")
        _git(other, "-c", "user.name=nft-snapshot", "-c", "user.email=nft-snapshot@example.com", "commit", "-q", "-m", "other")
        _git(other, "push", "origin", "HEAD:nft-data")
        with open(os.path.join(data, "nft-pilot", "c.txt"), "w") as fh:
            fh.write("c\n")
        retried = _run(data)
        combined = retried.stdout + retried.stderr
        self.assertEqual(retried.returncode, 0, combined)
        self.assertIn("push rejected, retry 1", combined)
        self.assertNotIn("no rebase in progress", combined)
        self.assertNotIn(TOKEN, combined)
        files = subprocess.check_output(["git", "-C", bare, "ls-tree", "-r", "--name-only", "nft-data"], text=True)
        self.assertIn("nft-pilot/a.txt", files)
        self.assertIn("nft-pilot/b.txt", files)
        self.assertIn("nft-pilot/c.txt", files)

    def test_auth_failure_does_not_rebase(self):
        tmp = tempfile.mkdtemp()
        data = os.path.join(tmp, "data")
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        log = os.path.join(tmp, "git-args")
        wrapper = os.path.join(bindir, "git")
        with open(wrapper, "w") as fh:
            fh.write("""#!/bin/bash
real=/usr/bin/git
printf '%s\\n' "$*" >> "$GIT_ARGS_LOG"
args=("$@")
if [ "${args[0]}" = "-c" ]; then
  printf '%s\\n' "${args[1]}" >> "$GIT_HEADER_LOG"
  args=("${args[@]:2}")
fi
cmd="${args[0]}"
printf 'CMD %s\\n' "$cmd" >> "$GIT_ARGS_LOG"
if [ "$cmd" = "push" ]; then
  echo "fatal: could not read Username for 'https://github.com': No such device or address" >&2
  exit 1
fi
if [ "$cmd" = "pull" ] || [ "$cmd" = "rebase" ]; then
  echo "unexpected $cmd" >&2
  exit 99
fi
exec "$real" "${args[@]}"
""")
        os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IEXEC)
        subprocess.run(["git", "init", "-b", "nft-data", data], check=True, capture_output=True, text=True)
        os.makedirs(os.path.join(data, "nft-pilot"))
        with open(os.path.join(data, "nft-pilot", "status.json"), "w") as fh:
            fh.write("{}\n")
        header_log = os.path.join(tmp, "header")
        proc = _run(data, {
            "PATH": bindir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_ARGS_LOG": log,
            "GIT_HEADER_LOG": header_log,
        })
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, combined)
        self.assertIn("push failed", combined)
        self.assertIn("could not read Username", combined)
        self.assertNotIn("no rebase in progress", combined)
        self.assertNotIn("push rejected", combined)
        self.assertNotIn(TOKEN, combined)
        commands = open(log).read()
        self.assertNotIn("CMD pull", commands)
        self.assertNotIn("CMD rebase", commands)
        header = open(header_log).read().strip().splitlines()[-1]
        self.assertTrue(header.startswith("http.https://github.com/.extraheader=AUTHORIZATION: basic "))
        self.assertNotIn("bearer", header.lower())
        self.assertNotIn(TOKEN, header)
        encoded = header.split("basic ", 1)[1]
        self.assertEqual(base64.b64decode(encoded).decode(), "x-access-token:" + TOKEN)
        self.assertIn("::add-mask::" + encoded, proc.stdout)
        self.assertNotIn(encoded, proc.stderr)

    def test_non_fast_forward_retries_and_other_failures_do_not(self):
        tmp = tempfile.mkdtemp()
        data = os.path.join(tmp, "data")
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        state = os.path.join(tmp, "pushes")
        wrapper = os.path.join(bindir, "git")
        with open(wrapper, "w") as fh:
            fh.write(r"""#!/bin/bash
real=/usr/bin/git
args=("$@")
if [ "${args[0]}" = "-c" ]; then
  printf '%s\n' "${args[1]}" >> "$GIT_HEADER_LOG"
  args=("${args[@]:2}")
fi
cmd="${args[0]}"
printf 'CMD %s\n' "$cmd" >> "$GIT_ARGS_LOG"
if [ "$cmd" = "push" ]; then
  n=$(wc -l < "$GIT_PUSH_COUNT")
  echo x >> "$GIT_PUSH_COUNT"
  if [ "$n" -eq 0 ]; then
    echo " ! [rejected]        HEAD -> nft-data (non-fast-forward)" >&2
    echo "error: failed to push some refs to 'https://github.com/example/repo.git'" >&2
    exit 1
  fi
  exit 0
fi
if [ "$cmd" = "pull" ]; then
  exit 0
fi
if [ "$cmd" = "rebase" ]; then
  echo "unexpected rebase" >&2
  exit 99
fi
exec "$real" "${args[@]}"
""")
        os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IEXEC)
        subprocess.run(["git", "init", "-b", "nft-data", data], check=True, capture_output=True, text=True)
        os.makedirs(os.path.join(data, "nft-pilot"))
        with open(os.path.join(data, "nft-pilot", "status.json"), "w") as fh:
            fh.write("{}\n")
        open(state, "w").close()
        log = os.path.join(tmp, "args")
        proc = _run(data, {
            "PATH": bindir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_ARGS_LOG": log,
            "GIT_HEADER_LOG": os.path.join(tmp, "header"),
            "GIT_PUSH_COUNT": state,
        })
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("push rejected, retry 1", combined)
        self.assertNotIn("no rebase in progress", combined)
        self.assertNotIn(TOKEN, combined)
        commands = open(log).read()
        self.assertEqual(commands.count("CMD push"), 2)
        self.assertEqual(commands.count("CMD pull"), 1)
        self.assertNotIn("CMD rebase", commands)

    def test_desk_data_is_refused(self):
        tmp = tempfile.mkdtemp()
        data = os.path.join(tmp, "data")
        subprocess.run(["git", "init", "-b", "desk-data", data], check=True, capture_output=True, text=True)
        os.makedirs(os.path.join(data, "nft-pilot"))
        with open(os.path.join(data, "nft-pilot", "status.json"), "w") as fh:
            fh.write("{}\n")
        proc = _run(data, {"TOKEN": ""})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("refusing to push the Solana branch", proc.stderr)
        self.assertNotIn("Username", proc.stderr)


if __name__ == "__main__":
    unittest.main()
