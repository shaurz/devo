import sys, os, traceback, wx
from functools import wraps
from wx.lib.agw import aui
from wx.lib.utils import AdjustRectToScreen

import dialogs
from async_wx import async_call, coroutine
from dirtree import DirTreeCtrl
from editor import Editor
from menu_defs import *
from util import frozen_window, is_text_file

from new_project_dialog import NewProjectDialog

class AppEnv(object):
    def __init__(self, mainframe):
        self.mainframe = mainframe

    @coroutine
    def OpenFile(self, path):
        if not (yield async_call(is_text_file, path)):
            dialogs.error(self.mainframe, "Selected file is not a text file:\n\n%s" % path)
        else:
            yield self.mainframe.OpenEditor(path)

    def NewEditor(self):
        self.mainframe.NewEditor()

def with_current_editor(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        editor = self.GetCurrentEditor()
        if editor is not None:
            return method(self, editor, *args, **kwargs)
    return wrapper

class MainFrame(wx.Frame):
    def __init__(self):
        rect = AdjustRectToScreen(wx.Rect(0, 0, 1000, 1500))
        if wx.Platform == "__WXGTK__":
            size = rect.Size
            pos = (0, 0)
        else:
            size = (rect.width, rect.height - 50)
            pos = (25, 25)

        wx.Frame.__init__(self, None, title="Devo", pos=pos, size=size)
        self.SetMenuBar(menubar.Create())

        self.editors = []
        self.env = AppEnv(self)

        self.manager = aui.AuiManager(self)
        nbstyle = (aui.AUI_NB_CLOSE_ON_ALL_TABS  | aui.AUI_NB_TOP | aui.AUI_NB_TAB_SPLIT
                  | aui.AUI_NB_TAB_MOVE | aui.AUI_NB_SCROLL_BUTTONS | wx.BORDER_NONE)
        self.notebook = aui.AuiNotebook(self, style=nbstyle)
        self.tree = DirTreeCtrl(self, self.env)

        self.manager.AddPane(self.tree,
            aui.AuiPaneInfo().Left().BestSize(wx.Size(200, -1)).CaptionVisible(False))
        self.manager.AddPane(self.notebook,
            aui.AuiPaneInfo().CentrePane())
        self.manager.Update()

        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(aui.EVT_AUINOTEBOOK_PAGE_CLOSE, self.OnPageClose)
        self.Bind(aui.EVT_AUINOTEBOOK_PAGE_CHANGED, self.OnPageChanged)

        self.Bind(wx.EVT_MENU, self.OnNewFile, id=wx.ID_NEW)
        self.Bind(wx.EVT_MENU, self.OnOpenFile, id=wx.ID_OPEN)
        self.Bind(wx.EVT_MENU, self.OnCloseFile, id=wx.ID_CLOSE)
        self.Bind(wx.EVT_MENU, self.OnClose, id=wx.ID_EXIT)

        self.Bind(wx.EVT_MENU, self.EditorAction("Save"), id=wx.ID_SAVE)
        self.Bind(wx.EVT_MENU, self.EditorAction("SaveAs"), id=wx.ID_SAVEAS)
        self.Bind(wx.EVT_MENU, self.EditorAction("Undo"), id=wx.ID_UNDO)
        self.Bind(wx.EVT_MENU, self.EditorAction("Redo"), id=wx.ID_REDO)
        self.Bind(wx.EVT_MENU, self.EditorAction("Cut"), id=wx.ID_CUT)
        self.Bind(wx.EVT_MENU, self.EditorAction("Copy"), id=wx.ID_COPY)
        self.Bind(wx.EVT_MENU, self.EditorAction("Paste"), id=wx.ID_PASTE)
        self.Bind(wx.EVT_MENU, self.EditorAction("SelectAll"), id=wx.ID_SELECTALL)
        self.Bind(wx.EVT_MENU, self.EditorAction("Find"), id=wx.ID_FIND)
        self.Bind(wx.EVT_MENU, self.EditorAction("FindNext"), id=ID_FIND_NEXT)
        self.Bind(wx.EVT_MENU, self.EditorAction("GoToLine"), id=ID_GO_TO_LINE)

        self.Bind(wx.EVT_MENU, self.OnNewProject, id=ID_NEW_PROJECT)

        self.Bind(wx.EVT_UPDATE_UI, self.EditorUpdateUI("GetModify"), id=wx.ID_SAVE)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_HasEditor, id=wx.ID_SAVEAS)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_HasEditor, id=wx.ID_CLOSE)
        self.Bind(wx.EVT_UPDATE_UI, self.EditorUpdateUI("CanUndo"), id=wx.ID_UNDO)
        self.Bind(wx.EVT_UPDATE_UI, self.EditorUpdateUI("CanRedo"), id=wx.ID_REDO)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_EditorHasSelection, id=wx.ID_CUT)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_EditorHasSelection, id=wx.ID_COPY)
        self.Bind(wx.EVT_UPDATE_UI, self.EditorUpdateUI("CanPaste"), id=wx.ID_PASTE)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_HasEditor, id=wx.ID_SELECTALL)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_HasEditor, id=wx.ID_FIND)
        self.Bind(wx.EVT_UPDATE_UI, self.EditorUpdateUI("CanFindNext"), id=ID_FIND_NEXT)
        self.Bind(wx.EVT_UPDATE_UI, self.UpdateUI_HasEditor, id=ID_GO_TO_LINE)

    def OnClose(self, evt):
        self.CloseSession(shutdown=True)

    @coroutine
    def CloseSession(self, shutdown=False):
        for i in xrange(self.notebook.GetPageCount()):
            editor = self.notebook.GetPage(i)
            try:
                if not (yield editor.TryClose()):
                    yield False
            except Exception:
                sys.stderr.write(traceback.format_exc())
                return
        if shutdown:
            self.Destroy()
        else:
            with frozen_window(self):
                for i in xrange(self.notebook.GetPageCount()-1, -1, -1):
                    self.notebook.DeletePage(i)
        yield True

    def OnPageClose(self, evt):
        evt.Veto()
        editor = self.notebook.GetPage(evt.GetSelection())
        self.ClosePage(editor)

    @coroutine
    def ClosePage(self, editor):
        if (yield editor.TryClose()):
            self.editors.remove(editor)
            with frozen_window(self.notebook):
                self.notebook.DeletePage(self.notebook.GetPageIndex(editor))

    def AddPage(self, win):
        self.editors.append(win)
        i = self.notebook.GetSelection() + 1
        self.notebook.InsertPage(i, win, win.title, select=True)
        win.sig_title_changed.bind(self.OnPageTitleChanged)
        win.SetFocus()

    def OnPageChanged(self, evt):
        editor = self.notebook.GetPage(evt.GetSelection())
        editor.SetFocus()

    def OnPageTitleChanged(self, win):
        i = self.notebook.GetPageIndex(win)
        if i != wx.NOT_FOUND:
            self.notebook.SetPageText(i, win.title)

    def NewEditor(self):
        editor = Editor(self.notebook, self.env)      
        with frozen_window(self.notebook):
            self.AddPage(editor)

    def FindEditor(self, path):
        for editor in self.editors:
            if editor.path == path:
                return editor

    def GetCurrentEditor(self):
        sel = self.notebook.GetSelection()
        if sel != wx.NOT_FOUND:
            return self.notebook.GetPage(sel)

    @coroutine
    def OpenEditor(self, path):
        realpath = os.path.realpath(path)
        editor = self.FindEditor(realpath)
        if editor is not None:
            i = self.notebook.GetPageIndex(editor)
            if i != wx.NOT_FOUND:
                self.notebook.SetSelection(i)
        else:
            editor = Editor(self.notebook, self.env, realpath)
            try:
                yield editor.LoadFile(realpath)
            except Exception, exn:
                dialogs.error(self, "Error opening file:\n\n%s" % exn)
                editor.Destroy()
            else:
                with frozen_window(self.notebook):
                    self.AddPage(editor)

    def OnNewFile(self, evt):
        self.NewEditor()

    def OnOpenFile(self, evt):
        path = dialogs.get_file_to_open(self)
        if path:
            self.OpenEditor(path)

    @with_current_editor
    def OnCloseFile(self, editor, evt):
        self.ClosePage(editor)

    def OnNewProject(self, evt):
        dlg = NewProjectDialog(self)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def EditorAction(self, method):
        def handler(evt):
            editor = self.GetCurrentEditor()
            if editor is not None:
                return getattr(editor, method)()
        return handler

    def EditorUpdateUI(self, method):
        def handler(evt):
            editor = self.GetCurrentEditor()
            if editor is not None:
                evt.Enable(getattr(editor, method)())
            else:
                evt.Enable(False)
        return handler

    def UpdateUI_HasEditor(self, evt):
        evt.Enable(self.GetCurrentEditor() is not None)

    def UpdateUI_EditorHasSelection(self, evt):
        editor = self.GetCurrentEditor()
        if editor is not None:
            start, end = editor.GetSelection()
            evt.Enable(start != end)
        else:
            evt.Enable(False)
