import sys, re, time
import wx
from async import async_call
from find_in_files import FindInFiles, make_matcher
from thread_output_ctrl import ThreadOutputCtrl

if sys.platform == "win32":
    r_path_start = re.compile(r"^[A-Za-z]:\\")
else:
    r_path_start = re.compile(r"^/")

class FindInFilesCtrl(wx.Panel):
    def __init__(self, parent, env):
        wx.Panel.__init__(self, parent)
        self.env = env
        self.max_line_length = 100
        self.finder = None

        self.output = ThreadOutputCtrl(self)

        self.status_label = wx.StaticText(self)
        button_stop = wx.Button(self, label="&Stop", size=(120, 25))
        button_clear = wx.Button(self, label="&Clear", size=(120, 25))
        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(self.status_label, 0, wx.ALIGN_CENTER)
        top_sizer.Add(button_stop, 0, wx.ALIGN_CENTER)
        top_sizer.AddSpacer(5)
        top_sizer.Add(button_clear, 0, wx.ALIGN_CENTER)
        top_sizer.AddStretchSpacer()

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(top_sizer, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(self.output, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self.Bind(wx.EVT_BUTTON, self.OnStop, button_stop)
        self.Bind(wx.EVT_BUTTON, self.OnClear, button_clear)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateStop, button_stop)
        self.Bind(wx.EVT_UPDATE_UI, self.OnUpdateClear, button_clear)
        self.output.Bind(wx.EVT_LEFT_DCLICK, self.OnLineDoubleClicked)

    def Destroy(self):
        if self.finder:
            self.finder.stop()
        wx.Panel.Destroy(self)

    def CanUndo(self):
        return self.output.CanUndo()

    def CanRedo(self):
        return self.output.CanRedo()

    def CanCut(self):
        return not self.output.GetReadOnly() and self.CanCopy()

    def CanCopy(self):
        start, end = self.output.GetSelection()
        return start != end

    def CanPaste(self):
        return self.output.CanPaste()

    def Undo(self):
        self.output.Undo()

    def Redo(self):
        self.output.Redo()

    def Cut(self):
        self.output.Cut()

    def Copy(self):
        self.output.Copy()

    def Paste(self):
        self.output.Paste()

    def SelectAll(self):
        self.output.SelectAll()

    def OnStop(self, evt):
        if self.finder:
            self.finder.stop()

    def OnUpdateStop(self, evt):
        evt.Enable(bool(self.finder))

    def OnClear(self, evt):
        self.output.ClearAll()

    def OnUpdateClear(self, evt):
        evt.Enable(not self.output.IsEmpty())

    def OnLineDoubleClicked(self, evt):
        cur_line = self.output.GetCurrentLine()
        s = self.output.GetLine(cur_line).strip()

        try:
            line_num = int(s.split(":", 1)[0])
            cur_line -= 1
        except ValueError:
            line_num = 1

        for cur_line in xrange(cur_line, -1, -1):
            path = self.output.GetLine(cur_line).rstrip()
            if r_path_start.match(path):
                self.env.open_file(path, line_num)
                break

    def find(self, details):
        if self.finder:
            self.finder.stop()

        matcher = make_matcher(details.find,
            case_sensitive=details.case, is_regexp=details.regexp)

        self.start_time = time.time()
        self.details = details
        self.finder = FindInFiles(details.path, matcher, self)
        async_call(self.finder.search)
        self.output.ClearAll()
        self.output.start()

    def add_file(self, filepath):
        self.output.write(filepath + "\n")

    def add_line(self, line_num, line):
        if len(line) > self.max_line_length:
            line = line[:self.max_line_length] + "..."
        self.output.write("  %d: %s\n" % (line_num, line))

    def end_file(self):
        self.output.write("\n")

    def __do_finish(self, finder):
        try:
            if finder is self.finder:
                self.details = None
                self.finder = None
                self.output.stop()
        except wx.PyDeadObjectError:
            pass

    def finish(self, finder, message):
        completed_time = time.time() - self.start_time
        self.output.write(message % (self.details.find, self.details.path, completed_time))
        wx.CallAfter(self.__do_finish, finder)

    def end_find(self, finder):
        self.finish(finder, "Search for '%s' in %s completed in %.2f seconds")

    def abort_find(self, finder):
        self.finish(finder, "Search for '%s' in %s aborted after %.2f seconds")
