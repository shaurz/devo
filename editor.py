import os
import wx
import wx.stc
from urllib import urlencode

import ID
from async import async_call, coroutine
from dialogs import dialogs
from fileutil import atomic_write_file, read_file, mkpath
from menu import MenuItem, MenuSeparator
from signal_wx import Signal
from styled_text_ctrl import StyledTextCtrl
from util import clean_text, shorten_text, set_clipboard_text

def decode_text(text):
    try:
        return text.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return text.decode("iso-8859-1"), "iso-8859-1"

def encode_text(text, encoding):
    try:
        return text.encode(encoding), encoding
    except UnicodeEncodeError:
        return text.encode("utf-8"), "utf-8"

class EditorSelectionWriter(object):
    def __init__(self, editor):
        self.editor = editor
        self.selection = editor.GetSelection()
        self.editor.SetReadOnly(True)

    def write(self, s):
        self.editor.SetReadOnly(False)
        self.editor.SetSelection(*self.selection)
        self.editor.ReplaceSelectionAndSelect(s)

    def close(self):
        self.editor.SetReadOnly(False)

class EditorAllTextWriter(object):
    def __init__(self, editor):
        self.editor = editor
        self.editor.SetReadOnly(True)

    def write(self, s):
        self.editor.SetReadOnly(False)
        line_num = self.editor.GetFirstVisibleLine()
        self.editor.SetText(s)
        self.editor.ScrollToLine(line_num)

    def close(self):
        self.editor.SetReadOnly(False)

class Editor(StyledTextCtrl, wx.FileDropTarget):
    def __init__(self, parent, env, path=""):
        StyledTextCtrl.__init__(self, parent, env)
        wx.FileDropTarget.__init__(self)
        self.SetDropTarget(self)

        self.path = path
        self.file_encoding = "utf-8"
        self.modified_externally = False
        self.static_title = None

        self.sig_title_changed = Signal(self)
        self.sig_status_changed = Signal(self)

        self.SetTabIndents(True)
        self.SetBackSpaceUnIndents(True)
        self.SetViewWhiteSpace(wx.stc.STC_WS_VISIBLEALWAYS)
        self.SetWhitespaceForeground(True, "#dddddd")
        self.SetEdgeMode(wx.stc.STC_EDGE_LINE)
        self.SetEdgeColumn(80)
        self.SetEdgeColour("#dddddd")

        self.Bind(wx.EVT_KEY_DOWN, self.OnKeyDown)
        self.Bind(wx.EVT_RIGHT_DOWN, self.OnRightDown)
        self.Bind(wx.stc.EVT_STC_SAVEPOINTLEFT, self.OnSavePointLeft)
        self.Bind(wx.stc.EVT_STC_SAVEPOINTREACHED, self.OnSavePointReached)
        self.Bind(wx.stc.EVT_STC_UPDATEUI, self.OnStcUpdateUI)

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def modified(self):
        return self.GetModify()

    @property
    def title(self):
        if self.static_title is not None:
            return self.static_title
        path = os.path.basename(self.path) or "Untitled"
        return "* " + path if self.modified else path

    @property
    def status_text(self):
        return "Line %d, Column %d" % (
            self.GetCurrentLine() + 1, self.GetColumn(self.GetCurrentPos()))

    @property
    def status_text_path(self):
        return self.static_title or self.path or "Untitled"

    @property
    def status_text_syntax(self):
        return "Syntax: " + self.syntax.description

    @property
    def dialog_parent(self):
        return wx.GetTopLevelParent(self)

    def GetModify(self):
        return (not self.GetReadOnly()) and (
                self.modified_externally or super(Editor, self).GetModify())

    def SetModified(self):
        self.modified_externally = True
        self.sig_title_changed.signal(self)

    @coroutine
    def TryClose(self):
        if self.modified:
            result = dialogs.ask_save_changes(self.dialog_parent, self.path)
            if result == wx.ID_YES:
                try:
                    save_result = (yield self.Save())
                    if save_result:
                        self.env.remove_monitor_path(self.path)
                    yield save_result
                except Exception:
                    yield False
            else:
                yield result == wx.ID_NO
        else:
            if self.path:
                self.env.remove_monitor_path(self.path)
            yield True

    def SetStatic(self, title, text):
        self.static_title = title
        self.path = ""
        self.SetText(text)
        self.SetSavePoint()
        self.EmptyUndoBuffer()
        self.SetReadOnly(True)
        self.sig_title_changed.signal(self)
        self.sig_status_changed.signal(self)

    @coroutine
    def LoadFile(self, path):
        self.SetReadOnly(True)
        self.Disable()

        old_path = self.path
        self.path = path
        self.sig_title_changed.signal(self)

        try:
            text = (yield async_call(read_file, path, "r"))
            text, self.file_encoding = decode_text(text)
            text = clean_text(text)

            self.modified_externally = False
            self.SetReadOnly(False)
            self.SetSyntaxFromFilename(path)
            self.SetText(text)
            self.SetSavePoint()

            if old_path:
                self.env.remove_monitor_path(old_path)
            self.env.add_monitor_path(path)
        except:
            self.path = old_path
            self.sig_title_changed.signal(self)
            self.sig_status_changed.signal(self)
            raise
        finally:
            self.Enable()
            self.SetReadOnly(False)

    @coroutine
    def TryLoadFile(self, path):
        try:
            yield self.LoadFile(path)
            self.EmptyUndoBuffer()
            yield True
        except Exception as e:
            dialogs.error(self.dialog_parent, "Error opening file:\n\n%s" % e)
            yield False

    @coroutine
    def Reload(self):
        line_num = self.GetFirstVisibleLine()
        yield self.LoadFile(self.path)
        self.ScrollToLine(line_num)

    @coroutine
    def WriteFile(self, path):
        def do_write_file(path, text):
            mkpath(os.path.dirname(path))
            atomic_write_file(path, text)

        text, self.file_encoding = encode_text(self.GetText(), self.file_encoding)
        text = clean_text(text)
        with self.env.updating_path(path):
            yield async_call(do_write_file, path, text)
        self.modified_externally = False
        self.SetSavePoint()

    def SetPath(self, path):
        if self.path:
            self.env.remove_monitor_path(path)
        self.path = path
        self.static_title = None
        self.SetSyntaxFromFilename(self.path)
        self.env.add_monitor_path(self.path)
        self.env.add_recent_file(self.path)
        self.sig_title_changed.signal(self)
        self.sig_status_changed.signal(self)

    def HasOpenFile(self):
        return bool(self.path)

    @coroutine
    def SaveAsInSameTab(self):
        path = self.env.get_file_to_save(path=os.path.dirname(self.path))
        if path:
            path = os.path.realpath(path)
            try:
                yield self.WriteFile(path)
            except Exception as e:
                dialogs.error(self.dialog_parent, "Error saving file '%s'\n\n%s" % (path, e))
                raise
            else:
                self.SetPath(path)
                yield True
        yield False

    @coroutine
    def SaveAsInNewTab(self):
        path = self.env.get_file_to_save(path=os.path.dirname(self.path))
        if path:
            path = os.path.realpath(path)
            editor = self.env.new_editor(path)
            editor.SetText(self.GetText())
            try:
                yield editor.WriteFile(path)
            except Exception as e:
                dialogs.error(self.dialog_parent, "Error saving file '%s'\n\n%s" % (path, e))
                raise
            else:
                editor.SetPath(path)

    def SaveAs(self):
        if self.path:
            return self.SaveAsInNewTab()
        else:
            return self.SaveAsInSameTab()

    @coroutine
    def Save(self):
        if self.path:
            try:
                yield self.WriteFile(self.path)
                self.env.add_monitor_path(self.path)
                yield True
            except Exception as e:
                dialogs.error(self.dialog_parent, "Error saving file '%s'\n\n%s" % (self.path, e))
                raise
        else:
            yield (yield self.SaveAsInSameTab())

    def OnReturnKeyDown(self, evt):
        start, end = self.GetSelection()
        if start == end:
            indent = self.GetLineIndentation(self.GetCurrentLine())
            pos = self.GetCurrentPos()
            if self.GetUseTabs():
                indent //= self.GetTabWidth()
                self.InsertText(pos, "\n" + "\t" * indent)
            else:
                self.InsertText(pos, "\n" + " " * indent)
            self.GotoPos(pos + indent + 1)
        else:
            evt.Skip()

    def OnKeyDown(self, evt):
        key = evt.GetKeyCode()
        mod = evt.GetModifiers()
        if mod == wx.MOD_NONE:
            if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
                self.OnReturnKeyDown(evt)
            else:
                evt.Skip()
        else:
            evt.Skip()

    def OnRightDown(self, evt):
        start, end = self.GetSelection()
        if start == end:
            pos = self.PositionFromPoint(evt.GetPosition())
            self.SetSelection(pos, pos)
            self.SetCurrentPos(pos)
        evt.Skip()

    def OnSavePointLeft(self, evt):
        self.sig_title_changed.signal(self)

    def OnSavePointReached(self, evt):
        self.sig_title_changed.signal(self)

    def OnStcUpdateUI(self, evt):
        self.sig_status_changed.signal(self)
        evt.Skip()

    def OnDropFiles(self, x, y, filenames):
        for filename in filenames:
            self.env.open_file(filename)
        return True

    @coroutine
    def OnModifiedExternally(self):
        if dialogs.ask_reload(self.dialog_parent, os.path.basename(self.path)):
            yield self.Reload()
        else:
            self.SetModified()

    @coroutine
    def OnUnloadedExternally(self):
        if os.path.exists(self.path):
            if dialogs.ask_reload(self.dialog_parent, os.path.basename(self.path)):
                yield self.Reload()
            else:
                self.SetModified()
        else:
            if dialogs.ask_unload(self.dialog_parent, os.path.basename(self.path)):
                self.env.close_view(self)
            else:
                self.SetModified()

    def GetDyanmicEditMenuItems(self):
        items = []
        if self.path:
            items.extend([
                MenuItem(ID.COPY_FILE_PATH, "Copy File Path"),
                MenuItem(ID.OPEN_CONTAINING_FOLDER, "Open Containing Folder"),
                MenuItem(ID.OPEN_IN_WEB_VIEW, "Preview in Web View"),
                MenuSeparator,
            ])
        selected = self.GetSelectedFirstLine()
        if selected:
            selected = shorten_text(selected, 40)
            items.append(MenuItem(ID.WEB_SEARCH, "Web Search for %s" % repr(selected)[1:]))
        return items

    def CopyFilePath(self):
        if self.path:
            set_clipboard_text(self.path)

    def OpenContainingFolder(self):
        if self.path:
            self.env.shell_open(os.path.dirname(self.path))

    def OpenPreview(self):
        if self.path:
            self.env.open_preview(self.path)

    def WebSearch(self):
        selected = self.GetSelectedFirstLine()
        if selected:
            url = "https://www.google.com/search?" + urlencode([("q", selected)])
            self.env.open_web_view(url)

    def GetSelectionWriter(self):
        if not self.GetReadOnly():
            return EditorSelectionWriter(self)

    def GetAllTextWriter(self):
        if not self.GetReadOnly():
            return EditorAllTextWriter(self)

    def SavePerspective(self):
        p = {
            "line"      : self.GetFirstVisibleLine(),
            "selection" : self.GetSelection(),
            "view_type" : "editor",
        }
        if self.path:
            p["path"] = self.path
        else:
            p["text"] = self.GetText()
            if self.static_title is not None:
                p["static_title"] = self.static_title
        return p

    @coroutine
    def LoadPerspective(self, p):
        if "text" in p:
            self.modified_externally = False
            static_title = p.get("static_title")
            if static_title is None:
                self.SetSavePoint()
                self.SetText(p["text"])
            else:
                self.SetStatic(static_title, p["text"])

        elif "path" in p:
            yield self.LoadFile(p["path"])
            self.EmptyUndoBuffer()

        self.ScrollToLine(p.get("line", 0))
        self.SetSelection(*p.get("selection", (0, 0)))
