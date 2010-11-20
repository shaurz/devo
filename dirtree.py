import sys, os, collections, stat, threading, time
import wx
from fsmonitor import FSMonitorThread, FSEvent

import fileutil
from async import async_call, coroutine, queued_coroutine, managed, CoroutineQueue, CoroutineManager
from dialogs import dialogs
from menu import Menu, MenuItem, MenuSeparator
from util import iter_tree_children, iter_tree_breadth_first, frozen_window, CallLater
from resources import load_bitmap

ID_EDIT = wx.NewId()
ID_OPEN = wx.NewId()
ID_RENAME = wx.NewId()
ID_DELETE = wx.NewId()
ID_NEW_FOLDER = wx.NewId()
ID_EXPAND_ALL = wx.NewId()
ID_COLLAPSE_ALL = wx.NewId()

file_context_menu = Menu("", [
    MenuItem(ID_NEW_FOLDER, "&New Folder"),
    MenuSeparator,
    MenuItem(ID_OPEN, "&Open"),
    MenuItem(ID_EDIT, "&Edit with Devo"),
    MenuItem(ID_RENAME, "&Rename"),
    MenuItem(ID_DELETE, "&Delete"),
    MenuSeparator,
    MenuItem(ID_EXPAND_ALL, "E&xpand All"),
    MenuItem(ID_COLLAPSE_ALL, "&Collapse All"),
])

dir_context_menu = Menu("", [
    MenuItem(ID_NEW_FOLDER, "&New Folder"),
    MenuSeparator,
    MenuItem(ID_OPEN, "&Open"),
    MenuItem(ID_RENAME, "&Rename"),
    MenuItem(ID_DELETE, "&Delete"),
    MenuSeparator,
    MenuItem(ID_EXPAND_ALL, "E&xpand All"),
    MenuItem(ID_COLLAPSE_ALL, "&Collapse All"),    
])

IM_FOLDER = 0
IM_FOLDER_DENIED = 1
IM_FILE = 2

def dirtree_insert(tree, parent_item, text, image):
    i = 0
    text_lower = text.lower()
    for i, item in enumerate(iter_tree_children(tree, parent_item)):
        item_text = tree.GetItemText(item)
        if item_text == text:
            return item
        if image != IM_FILE and tree.GetItemImage(item) == IM_FILE:
            return tree.InsertItemBefore(parent_item, i, text, image)
        if item_text.lower() > text_lower:
            if not (image == IM_FILE and tree.GetItemImage(item) != IM_FILE):
                return tree.InsertItemBefore(parent_item, i, text, image)
    return tree.AppendItem(parent_item, text, image)

def dirtree_delete(tree, parent_item, text):
    for item in iter_tree_children(tree, parent_item):
        if text == tree.GetItemText(item):
            sel_item = tree.GetNextSibling(item)
            if not sel_item.IsOk():
                sel_item = tree.GetPrevSibling(item)
            if sel_item.IsOk():
                tree.SelectItem(sel_item)
            tree.Delete(item)
            break

NODE_UNPOPULATED = 0
NODE_POPULATING = 1
NODE_POPULATED = 2

class SimpleNode(object):
    type = 'd'
    path = ""
    context_menu = None

    def __init__(self, label, children):
        self.label = label
        self.children = children
        self.state = NODE_UNPOPULATED
        self.item = None

    def expand(self, tree, monitor, filter):
        if self.state == NODE_UNPOPULATED:
            tree.SetItemImage(self.item, IM_FOLDER)
            for node in self.children:
                item = tree.AppendItem(self.item, node.label, IM_FOLDER)
                tree.SetItemNode(item, node)
                tree.SetItemHasChildren(item, True)
            self.state = NODE_POPULATED

    def collapse(self, tree, monitor):
        pass

class FileInfo(collections.namedtuple("FileInfo", "filename dirpath stat_result listable hidden")):
    @property
    def path(self):
        return os.path.join(self.dirpath, self.filename)
    @property
    def is_file(self):
        return stat.S_ISREG(self.stat_result.st_mode)
    @property
    def is_dir(self):
        return stat.S_ISDIR(self.stat_result.st_mode)

def get_file_info(dirpath, filename):
    path = os.path.join(dirpath, filename)
    stat_result = os.stat(path)
    hidden = fileutil.is_hidden_file(path)
    if stat.S_ISDIR(stat_result.st_mode):
        try:
            listable = os.access(path, os.X_OK)
        except OSError:
            listable = False
    else:
        listable = False
    return FileInfo(filename, dirpath, stat_result, listable, hidden)

def listdir(dirpath):
    result = []
    for filename in os.listdir(dirpath):
        try:
            result.append(get_file_info(dirpath, filename))
        except OSError:
            pass
    result.sort(key=lambda info: info.filename.lower())
    return result

class FSNode(object):
    __slots__ = ("state", "path", "type", "item", "watch", "label")

    def __init__(self, path, type, label=""):
        self.state = NODE_UNPOPULATED
        self.path = path
        self.type = type
        self.item = None
        self.watch = None
        self.label = label or os.path.basename(path) or path

    @coroutine
    def _do_expand(self, tree, monitor, filter):
        self.watch = monitor.add_dir_watch(self.path, user=self)
        dirs = []
        files = []            
        for info in (yield async_call(listdir, self.path)):
            if not filter(info):
                continue
            path = os.path.join(self.path, info.filename)
            if info.is_file:
                files.append(FSNode(path, 'f'))
            elif info.is_dir:
                dirs.append((FSNode(path, 'd'), info.listable))
        for node, listable in dirs:
            image = IM_FOLDER if listable else IM_FOLDER_DENIED
            item = tree.AppendItem(self.item, node.label, image)
            tree.SetItemNode(item, node)
            tree.SetItemHasChildren(item, listable)
        for node in files:
            item = tree.AppendItem(self.item, node.label, IM_FILE)
            tree.SetItemNode(item, node)
        tree.SetItemImage(self.item, IM_FOLDER)
        tree.SetItemHasChildren(self.item, tree.GetFirstChild(self.item)[0].IsOk())

    @coroutine
    def expand(self, tree, monitor, filter):
        if self.state == NODE_UNPOPULATED:
            self.state = NODE_POPULATING
            yield self._do_expand(tree, monitor, filter)
            self.state = NODE_POPULATED

    def collapse(self, tree, monitor):
        pass
        #monitor.remove_watch(self.watch)
        #self.populated = False

    @coroutine
    def add(self, name, tree, monitor, filter):
        if self.state == NODE_POPULATED:
            try:
                info = (yield async_call(get_file_info, self.path, name))
            except OSError, e:
                return
            if not filter(info):
                return
            if info.is_file:
                type = 'f'
                image = IM_FILE
            elif info.is_dir:
                type = 'd'
                image = IM_FOLDER
            item = dirtree_insert(tree, self.item, name, image)
            node = FSNode(info.path, type)
            tree.SetItemNode(item, node)
            if type == 'd':
                tree.SetItemHasChildren(item, True)
            tree.SetItemHasChildren(self.item, True)
            yield item

    def remove(self, name, tree, monitor):
        if self.state == NODE_POPULATED:
            dirtree_delete(tree, self.item, name)

    @property
    def context_menu(self):
        if self.type == 'f':
            return file_context_menu
        elif self.type == 'd':
            return dir_context_menu

def DirNode(path):
    return FSNode(path, 'd')

class DirTreeFilter(object):
    def __init__(self, show_hidden=False, show_files=True, show_dirs=True):
        self.show_hidden = show_hidden
        self.show_files = show_files
        self.show_dirs = show_dirs
        self.hidden_exts = [".pyc", ".pyo", ".o", ".a", ".obj", ".lib", ".swp", "~"]
        self.hidden_dirs = ["CVS"]

    def __call__(self, info):
        if info.hidden and not self.show_hidden:
            return False
        if info.is_file and not self.show_files:
            return False
        elif info.is_dir:
            if not self.show_dirs:
                return False
            if info.filename in self.hidden_dirs:
                return False
        for ext in self.hidden_exts:
            if info.filename.endswith(ext):
                return False
        if info.filename.startswith(".#"):
            return False
        return True

def MakeTopLevel():
    if sys.platform == "win32":
        import win32api
        from win32com.shell import shell, shellcon
        mycomputer = SimpleNode("My Computer",
                        [FSNode(drive, 'd') for drive in
                         win32api.GetLogicalDriveStrings().strip("\0").split("\0")])
        mydocs = shell.SHGetFolderPath(None, shellcon.CSIDL_PERSONAL, None, 0)
        desktop = shell.SHGetFolderPath(None, shellcon.CSIDL_DESKTOP, None, 0)
        return [mycomputer, FSNode(mydocs, 'd'), FSNode(desktop, 'd')]
    else:
        return [FSNode("/", 'd')]

class DirTreeCtrl(wx.TreeCtrl):
    def __init__(self, parent, env, toplevel=None, filter=None, show_root=False):
        style = wx.TR_DEFAULT_STYLE | wx.TR_EDIT_LABELS | wx.BORDER_NONE
        if not show_root:
            style |= wx.TR_HIDE_ROOT

        wx.TreeCtrl.__init__(self, parent, style=style)
        self.SetDoubleBuffered(True)
        self.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        self.env = env
        self.toplevel = toplevel or MakeTopLevel()
        self.filter = filter or DirTreeFilter()
        self.cq = CoroutineQueue()
        self.cm = CoroutineManager()
        self.select_later_parent = None
        self.select_later_name = None
        self.select_later_time = 0
        self.expanding_all = False

        self.imglist = wx.ImageList(16, 16)
        self.imglist.Add(load_bitmap("icons/folder.png"))
        self.imglist.Add(load_bitmap("icons/folder_denied.png"))
        self.imglist.Add(load_bitmap("icons/file.png"))
        self.SetImageList(self.imglist)

        self.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.OnItemActivated)
        self.Bind(wx.EVT_TREE_ITEM_EXPANDING, self.OnItemExpanding)
        self.Bind(wx.EVT_TREE_ITEM_COLLAPSED, self.OnItemCollapsed)
        self.Bind(wx.EVT_TREE_ITEM_RIGHT_CLICK, self.OnItemRightClicked)
        self.Bind(wx.EVT_TREE_BEGIN_LABEL_EDIT, self.OnItemBeginLabelEdit)
        self.Bind(wx.EVT_TREE_END_LABEL_EDIT, self.OnItemEndLabelEdit)
        self.Bind(wx.EVT_TREE_BEGIN_DRAG, self.OnBeginDrag)
        self.Bind(wx.EVT_MENU, self.OnItemEdit, id=ID_EDIT)
        self.Bind(wx.EVT_MENU, self.OnItemOpen, id=ID_OPEN)
        self.Bind(wx.EVT_MENU, self.OnItemRename, id=ID_RENAME)
        self.Bind(wx.EVT_MENU, self.OnItemDelete, id=ID_DELETE)
        self.Bind(wx.EVT_MENU, self.OnNewFolder, id=ID_NEW_FOLDER)
        self.Bind(wx.EVT_MENU, self.OnExpandAll, id=ID_EXPAND_ALL)
        self.Bind(wx.EVT_MENU, self.OnCollapseAll, id=ID_COLLAPSE_ALL)

        self.monitor = FSMonitorThread()
        self.monitor_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnMonitorTimer, self.monitor_timer)
        self.monitor_timer.Start(100)

    def Destroy(self):
        self.monitor_timer.Stop()
        self.monitor.remove_all_watches()
        self.monitor.read_events()
        self.cm.cancel()
        wx.TreeCtrl.Destroy(self)

    def OnMonitorTimer(self, evt):
        events = self.monitor.read_events()
        if events:
            self.OnFileSystemChanged(events)

    def SelectLater(self, parent, name, timeout=1):
        self.select_later_parent = parent
        self.select_later_name = name
        self.select_later_time = time.time() + timeout

    @managed("cm")
    @queued_coroutine("cq")
    def OnFileSystemChanged(self, events):
        for evt in events:
            if evt.action in (FSEvent.Create, FSEvent.MoveTo):
                item = (yield evt.user.add(evt.name, self, self.monitor, self.filter))
                if item:
                    if evt.name == self.select_later_name \
                    and self.GetItemParent(item) == self.select_later_parent:
                        if time.time() < self.select_later_time:
                            self.SelectItem(item)
                        self.select_later_name = None
                        self.select_later_parent = None
                        self.select_later_time = 0
            elif evt.action in (FSEvent.Delete, FSEvent.MoveFrom):
                evt.user.remove(evt.name, self, self.monitor)

    def OnItemActivated(self, evt):
        node = self.GetEventNode(evt)
        if node.type == 'f':
            self.env.open_file(node.path)
        elif node.type == 'd':
            self.Toggle(node.item)

    def OnItemExpanding(self, evt):
        node = self.GetEventNode(evt)
        if node.type == 'd' and node.state == NODE_UNPOPULATED:
            self.ExpandNode(node)

    def OnItemCollapsed(self, evt):
        node = self.GetEventNode(evt)
        if node.type == 'd':
            self.CollapseNode(node)

    def OnItemRightClicked(self, evt):
        self.SelectItem(evt.GetItem())
        node = self.GetEventNode(evt)
        menu = node.context_menu
        if menu:
            self.PopupMenu(menu.Create())

    def OnItemBeginLabelEdit(self, evt):
        node = self.GetEventNode(evt)
        if not (node and node.path):
            evt.Veto()

    def OnItemEndLabelEdit(self, evt):
        if not evt.IsEditCancelled():
            evt.Veto()
            node = self.GetEventNode(evt)
            self.RenameNode(node, evt.GetLabel())

    def OnBeginDrag(self, evt):
        node = self.GetEventNode(evt)
        if node:
            data = wx.FileDataObject()
            data.AddFile(node.path)
            dropsrc = wx.DropSource(self)
            dropsrc.SetData(data)
            dropsrc.DoDragDrop()

    def OnItemEdit(self, evt):
        node = self.GetSelectedNode()
        if node and node.type == 'f':
            self.env.open_file(node.path)

    def OnItemOpen(self, evt):
        path = self.GetSelectedPath()
        if path:
            self._shell_open(path)

    def OnItemRename(self, evt):
        node = self.GetSelectedNode()
        if node and node.path:
            self.EditLabel(node.item)

    def OnItemDelete(self, evt):
        node = self.GetSelectedNode()
        if node:
            next_item = self.GetNextSibling(node.item)
            fileutil.shell_remove(self, node.path)

    def OnNewFolder(self, evt):
        node = self.GetSelectedNode()
        if node:
            if node.type != 'd':
                node = self.GetPyData(self.GetItemParent(node.item))
            name = dialogs.get_text_input(self,
                "New Folder",
                "Please enter new folder name:")
            if name:
                self.NewFolder(node, name)

    @coroutine
    def OnExpandAll(self, evt):
        del evt
        if self.expanding_all:
            return
        self.expanding_all = True
        try:
            for item in iter_tree_breadth_first(self, self.GetRootItem()):
                if not self.expanding_all:
                    return
                node = self.GetPyData(item)
                if node.type == 'd':
                    if node.state == NODE_UNPOPULATED:
                        try:
                            yield self.ExpandNode(node)
                        except Exception:
                            pass
                    else:
                        self.Expand(item)
        finally:
            self.expanding_all = False

    def OnCollapseAll(self, evt):
        self.expanding_all = False
        self.CollapseAll()

    def SetItemNode(self, item, node):
        self.SetPyData(item, node)
        node.item = item
        return node

    def GetEventNode(self, evt):
        item = evt.GetItem()
        if item.IsOk():
            return self.GetPyData(item)

    def GetSelectedNode(self):
        item = self.GetSelection()
        if item.IsOk():
            return self.GetPyData(item)

    def GetSelectedPath(self):
        node = self.GetSelectedNode()
        return node.path if node else ""

    # Workaround for Windows sillyness
    if wx.Platform == "__WXMSW__":
        def IsExpanded(self, item):
            return self.GetRootItem() == item or wx.TreeCtrl.IsExpanded(self, item)
        def Expand(self, item):
            if self.GetRootItem() != item:
                wx.TreeCtrl.Expand(self, item)

    @managed("cm")
    @queued_coroutine("cq")
    def PopulateNode(self, node):
        if node.state == NODE_UNPOPULATED:
            yield node.expand(self, self.monitor, self.filter)

    @managed("cm")
    @coroutine
    def ExpandNode(self, node):
        if node.state == NODE_UNPOPULATED:
            yield self.PopulateNode(node)
        if node.state != NODE_POPULATING:
            self.Expand(node.item)

    def CollapseNode(self, node):
        node.collapse(self, self.monitor)

    @managed("cm")
    @coroutine
    def RenameNode(self, node, name):
        newpath = os.path.join(os.path.dirname(node.path), name)
        if newpath != node.path:
            try:
                if (yield async_call(os.path.exists, newpath)):
                    if not dialogs.ask_overwrite(self, newpath):
                        return
                yield async_call(fileutil.rename, node.path, newpath)
                self.SelectLater(self.GetItemParent(node.item), name)
            except OSError, e:
                dialogs.error(self, str(e))

    @managed("cm")
    @coroutine
    def NewFolder(self, node, name):
        path = os.path.join(node.path, name)
        try:
            yield async_call(os.mkdir, path)
            if self.IsExpanded(node.item):
                self.SelectLater(node.item, name)
        except OSError, e:
            dialogs.error(self, str(e))

    @managed("cm")
    def _shell_open(self, path):
        try:
            return async_call(fileutil.shell_open, path, workdir=os.path.dirname(path))
        except OSError, e:
            dialogs.error(self, str(e))

    @managed("cm")
    @coroutine
    def _InitialExpand(self, rootnode):
        yield self.ExpandNode(rootnode)
        if isinstance(self.toplevel[0], SimpleNode):
            yield self.ExpandNode(self.toplevel[0])

    def SetTopLevel(self, toplevel=None):
        self.monitor.remove_all_watches()
        self.monitor.read_events()
        self.cm.cancel()
        self.DeleteAllItems()
        self.toplevel = toplevel or MakeTopLevel()
        if len(self.toplevel) == 1:
            rootitem = self.AddRoot(self.toplevel[0].label)
            rootnode = self.toplevel[0]
        else:
            rootitem = self.AddRoot("")
            rootnode = SimpleNode("", self.toplevel)
        self.SetItemNode(rootitem, rootnode)
        return self._InitialExpand(rootnode)

    def _FindExpandedPaths(self, item, path, expanded):
        if self.IsExpanded(item):
            node = self.GetPyData(item)
            if node.type == 'd':
                subpath = (path and path + "/") + self.GetItemText(item)
                len_expanded = len(expanded)
                for child_item in iter_tree_children(self, item):
                    self._FindExpandedPaths(child_item, subpath, expanded)
                if len(expanded) == len_expanded:
                    expanded.append(subpath)
        return expanded

    def FindExpandedPaths(self):
        expanded = []
        for item in iter_tree_children(self, self.GetRootItem()):
            self._FindExpandedPaths(item, "", expanded)
        return expanded

    @managed("cm")
    @coroutine
    def _ExpandPaths(self, item, paths):
        expanded = [path[0] for path in paths if path]
        sub_paths = [path[1:] for path in paths if len(path) > 1]
        yield self.ExpandNode(self.GetPyData(item))
        for child_item in iter_tree_children(self, item):
            if self.GetItemText(child_item) in expanded:
                yield self._ExpandPaths(child_item, sub_paths)

    def ExpandPaths(self, paths):
        paths = [path.strip("/").split("/") for path in paths]
        return self._ExpandPaths(self.GetRootItem(), paths)

    def ExpandPath(self, path):
        return self._ExpandPaths(self.GetRootItem(), [path])

    def _SelectPath(self, item, path):
        node = self.GetPyData(item)
        if node.path == path:
            self.SelectItem(node.item)
        elif node.type == 'd':
            for child_item in iter_tree_children(self, item):
                self._SelectPath(child_item, path)

    @managed("cm")
    @coroutine
    def SelectPath(self, path):
        yield self.ExpandPath(path)
        self._SelectPath(self.GetRootItem(), path)

    def SavePerspective(self):
        p = {}
        expanded = self.FindExpandedPaths()
        if expanded:
            p["expanded"] = expanded
        selected = self.GetSelectedPath()
        if selected:
            p["selected"] = selected
        return p

    @managed("cm")
    @coroutine
    def LoadPerspective(self, p):
        expanded = p.get("expanded", ())
        if expanded:
            yield self.ExpandPaths(expanded)
        if "selected" in p:
            yield self.SelectPath(p["selected"])
