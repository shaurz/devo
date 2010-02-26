import os
import stat
import wx

import dialogs
from async_wx import async_call, coroutine, queued_coroutine, managed_coroutine,  CoroutineQueue, CoroutineManager
from util import iter_tree_children
from resources import load_bitmap

class FSNode(object):
    __slots__ = ("path", "type", "populated")

    def __init__(self, path, type):
        self.path = path
        self.type = type
        self.populated = False

IM_FOLDER = 0
IM_FOLDER_DENIED = 1
IM_FOLDER_WAITING = 2
IM_FILE = 3

class DirTreeCtrl(wx.TreeCtrl):
    def __init__(self, parent, env, rootdir):
        style = wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.BORDER_NONE
        wx.TreeCtrl.__init__(self, parent, style=style)
        self.env = env
        self.rootdir = rootdir
        self.cq_PopulateNode = CoroutineQueue()
        self.cm = CoroutineManager()
        self.imglist = wx.ImageList(16, 16)
        self.imglist.Add(load_bitmap("icons/folder.png"))
        self.imglist.Add(load_bitmap("icons/folder_denied.png"))
        self.imglist.Add(load_bitmap("icons/folder_waiting.png"))
        self.imglist.Add(load_bitmap("icons/file.png"))
        self.SetImageList(self.imglist)
        self.InitializeTree()
        self.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.OnItemActivated)
        self.Bind(wx.EVT_TREE_ITEM_EXPANDING, self.OnItemExpanding)
        self.Bind(wx.EVT_CLOSE, self.OnClose)

    def OnClose(self, evt):
        print "closing"
        self.cq_PopulateNode.cancel()
        self.cm.cancel()

    def OnItemActivated(self, evt):
        item = evt.GetItem()
        node = self.GetPyData(item)
        if node.type == 'f':
            self.env.OpenFile(node.path)
        elif node.type == 'd':
            self.Toggle(item)

    def OnItemExpanding(self, evt):
        item = evt.GetItem()
        node = self.GetPyData(item)
        if node.type == 'd' and not node.populated:
            self.PopulateNode(item, node)

    @queued_coroutine("cq_PopulateNode")
    def PopulateNode(self, rootitem, rootnode):
        rootnode.populated = True
        for item in iter_tree_children(self, rootitem):
            node = self.GetPyData(item)
            if node.type == 'd':
                try:
                    yield self.PopulateDirTree(item, node.path)
                except OSError:
                    self.SetItemImage(item, IM_FOLDER_DENIED)

    @managed_coroutine("cm")
    def PopulateDirTree(self, rootitem, rootpath):
        files = []
        for filename in sorted((yield async_call(os.listdir, rootpath)), key=lambda x: x.lower()):
            path = os.path.join(rootpath, filename)
            try:
                st = (yield async_call(os.stat, path))
            except OSError:
                pass
            else:
                if stat.S_ISREG(st.st_mode):
                    files.append((filename, path))
                elif stat.S_ISDIR(st.st_mode):
                    item = self.AppendItem(rootitem, filename, IM_FOLDER_WAITING)
                    self.SetPyData(item, FSNode(path, 'd'))
        for filename, path in files:
            item = self.AppendItem(rootitem, filename, IM_FILE)
            self.SetPyData(item, FSNode(path, 'f'))
        self.SetItemImage(rootitem, IM_FOLDER)

    @managed_coroutine("cm")
    def InitializeTree(self):
        self.DeleteAllItems()
        rootitem = self.AddRoot("")
        rootnode = FSNode(self.rootdir, 'd')
        self.SetPyData(rootitem, rootnode)
        try:
            yield self.PopulateDirTree(rootitem, self.rootdir)
            yield self.PopulateNode(rootitem, rootnode)
        except OSError, exn:
            dialogs.error(self, "Error: %s" % exn)
