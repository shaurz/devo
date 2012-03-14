import sys, os, subprocess

def make_environment(env=None):
    if env is None:
        env = os.environ
    env = env.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "UTF-8"
    return env

def run_shell_command(cmdline, pipe_output=True, env=None, **kwargs):
    if sys.platform == "win32":
        args = cmdline
    else:
        args = [os.environ.get("SHELL", "/bin/sh")]

    process = subprocess.Popen(args,
        stdin = subprocess.PIPE if sys.platform != "win32" else None,
        stdout = subprocess.PIPE if pipe_output else None,
        stderr = subprocess.STDOUT if pipe_output else None,
        bufsize = 1,
        close_fds = (sys.platform != "win32"),
        shell = (sys.platform == "win32"),
        env = make_environment(env),
        **kwargs)

    if sys.platform != "win32":
        process.stdin.write(cmdline)
        process.stdin.close()

    return process

def kill_shell_process(process, force=False):
    if sys.platform != "win32":
        signal = "-KILL" if force else "-TERM"
        rc = subprocess.call(["pkill", signal, "-P", str(process.pid)])
        if rc == 0:
            return

    if force:
        process.kill()
    else:
        process.terminate()
