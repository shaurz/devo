import os
import win32api, win32con, win32file, pywintypes

# os.rename is broken on windows
def rename(old, new):
    try:
        win32file.MoveFileEx(old, new, win32file.MOVEFILE_REPLACE_EXISTING)
    except pywintypes.error, e:
        raise WindowsError(*e.args)

def shell_open(path, workdir=None):
    return win32api.ShellExecute(None, "open", path, None, workdir, win32con.SW_SHOW) > 32

def get_user_config_dir(name=""):
    path = os.environ["APPDATA"]
    if name:
        path = os.path.join(path, name)
    return path

def is_hidden_file(path):
    return (win32file.GetFileAttributes(path) & win32file.FILE_ATTRIBUTE_HIDDEN) != 0

__all__ = (
    "rename",
    "shell_open",
    "get_user_config_dir",
    "is_hidden_file",
)
