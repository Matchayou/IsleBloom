"""Git plumbing in a service-owned repository, never a user's configuration."""
import os
import subprocess
from pathlib import Path

ENV = {'PATH': '/usr/bin:/bin:/usr/local/bin', 'LANG': 'C.UTF-8',
       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
       'GIT_TERMINAL_PROMPT': '0', 'GIT_ATTR_NOSYSTEM': '1',
       'GIT_AUTHOR_NAME': 'Code service', 'GIT_AUTHOR_EMAIL': 'code@localhost',
       'GIT_COMMITTER_NAME': 'Code service', 'GIT_COMMITTER_EMAIL': 'code@localhost'}


def git(root, *args, data=None, check=True):
    command = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
               '-c', 'core.attributesFile=/dev/null', '-c', 'commit.gpgSign=false',
               '-c', 'core.autocrlf=false', '-c', 'protocol.file.allow=always', '-C', str(root), *args]
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=ENV, timeout=30)
    if check and result.returncode:
        raise ValueError('Controlled Git operation failed: ' + result.stderr.decode(errors='replace')[:1500])
    return result


def output(root, *args, **kwargs):
    return git(root, *args, **kwargs).stdout.decode().strip()
