import time
import datetime

from .notify_gui import open_notify_gui

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango, GdkPixbuf

class base_run_context(object):
    """ This class deals with the GUI side of running the actual tests. """
    def __init__(self, context, run_group_manager_class):

        builder = context.builder
        self.context = context

        # Define Windows
        self.main_window = builder.get_object("TestGroupRunnerMain")
        self.info_window = builder.get_object("TestGroupRunnerInfo")

        # Define labels
        self.run_labels = (
            builder.get_object("run_lab_main"),
            builder.get_object("run_lab_info")
        )
        for label in self.run_labels:
            label.modify_font(Pango.FontDescription('Mono'))

        # Define progress bars
        self.progress_bars = (
            builder.get_object("running_time_main"),
            builder.get_object("running_time_info")
        )

        """ Define TestGroupRunnerMain Objects """

        # Define running list of tests in main window
        self.test_list = builder.get_object("RunTestsList")
        self.test_list.set_model(Gtk.ListStore(str, GdkPixbuf.Pixbuf, object))
        self.test_list.append_column(Gtk.TreeViewColumn("Test", Gtk.CellRendererText(), text=0))
        self.test_list.append_column(Gtk.TreeViewColumn("Status", Gtk.CellRendererPixbuf(), pixbuf=1))
        selection = self.test_list.get_selection()
        selection.connect("changed", lambda sel : self.load_info())

        # Define buttons
        self.run_ok_btn = builder.get_object("run_ok_btn")
        self.run_cancel_btn = builder.get_object("run_cancel_btn")
        self.redo_btn = builder.get_object("run_redo_btn")
        self.unfreeze_btn = builder.get_object("run_unfreeze_btn") # AKA "Resume"
        self.info_btn = builder.get_object("run_info_btn") # AKA "Output"

        # Define button connections
        self.run_ok_btn.connect("clicked",     lambda btn: self.on_ok())
        self.run_cancel_btn.connect("clicked", lambda btn: self.on_cancel())
        self.redo_btn.connect("clicked",       lambda btn: self.on_redo())
        self.unfreeze_btn.connect("clicked",   lambda btn: self.on_unfreeze())
        self.info_btn.connect("clicked",       lambda btn: self.on_info())

        """ Define TestGroupRunnerInfo Objects """

        # Define buttons
        self.info_back_btn = builder.get_object("info_back_btn")
        self.info_back_btn.connect("clicked", lambda btn: self.on_back())

        # Define status area
        self.info_status_box = builder.get_object("test_info_status_box")
        self.info_status_spinner = builder.get_object("test_info_status_spinner")
        self.info_status_label = builder.get_object("test_info_status_label")
        # Spinner is replaced with this pass/fail icon when the test finishes
        self.info_status_icon = builder.get_object("info_status_img")

        # Define textviews and buffers
        self.out_text = builder.get_object("test_output_text")
        self.log_text = builder.get_object("test_log_text")
        self.out_text.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))
        self.log_text.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))

        is_desktop = True if context.args['desktop'] else False
        self.out_text.set_sensitive(is_desktop)
        self.log_text.set_sensitive(is_desktop)

        self.out_text.connect("size-allocate", self._scroll_to_end)
        self.log_text.connect("size-allocate", self._scroll_to_end)

        # Define Buffers
        self.out_buf = self.out_text.get_buffer()
        self.log_buf = self.log_text.get_buffer()

        # Define tags
        self.good_tag = self.out_buf.create_tag("good", foreground="#00FF00")
        self.bad_tag  = self.out_buf.create_tag("bad", foreground="#FF0000")
        self.norm_tag = self.out_buf.create_tag("norm", foreground="#FFFFFF")
        self.err_tag  = self.log_buf.create_tag("error", foreground="#FF0000")
        self.warn_tag = self.log_buf.create_tag("warn", foreground="#FFFF00")
        self.info_tag = self.log_buf.create_tag("info", foreground="#FFFFFF")

        """
        The run group manager is the base_run_group_manager class
        defined in dt_db_base/base_run_test_group.py.
        It is responsible for the actual running of the test process,
        interpreting the output line-by-line, and running any needed callbacks.
        Here, we define any callbacks we need to run under various conditions.
        """
        self.run_group_man = run_group_manager_class(
            context,
            self._good_line,  self._bad_line,  self._normal_line,
            self._info_line,  self._warning_line,  self._error_line,
            {
             "FINISHED":      lambda args:     self.finished(),
             "SELECT_TEST":   lambda testfile: self.select_testfile(testfile),
             "SELECT_DEV" :   lambda dev_uuid: self.select_dev(dev_uuid),
             "START_OUTPUT":  lambda outfile:  self.start_outfile(outfile),
             "START_LOGFILE": lambda logfile:  self.start_logfile(logfile),
             "STATUS_TEST":   lambda args:     self.test_status(args),
             "FREEZE":        lambda args:     self.freeze(),
            }
        )

        self.total_duration = None
        self.test_time = 0
        self.total_test_time = 0
        self.test_start_time = 0

        self.last_test_result = False

        self.current_dev = None
        self.current_test = None
        self.tests = []

        """
        We call GLib.timeout_add() to run update_progress() at regular intervals,
        which updates the progress bar. This variable stores that job ID later.
        """
        self.update_id = None

        context.on_exit_cbs.insert(0, self.force_stop)

        context.view_objs["RunGroupViewObj"] = self

    def show_view(self):
        self.context.force_view("TestGroupRunnerMain")
        self.unfreeze_btn.hide()
        if not self.run_group_man.readonly:
            self.start_test_group()
        self.info_status_spinner.start()

    def update_status_time(self):
        """ Calculate the elapsed test time, update the title label """

        # If the test has stopped, stop updating progress bar, remove grey-out on test list
        if not self.run_group_man.live:
            self._stop_update()
            self.test_list.set_sensitive(True)
            return

        total_seconds = time.time() - self.test_start_time
        total_minutes = int(total_seconds / 60)
        total_hours   = total_minutes / 60
        total_minutes = total_minutes % 60
        total_sec_frac = int(total_seconds * 100) % 100
        total_seconds = int(total_seconds % 60)
        self.update_run_lab((total_hours, total_minutes, total_seconds, total_sec_frac))

    def finished(self):

        self.run_group_man.wait_for_end()

        self.run_ok_btn.set_sensitive(True)
        self.test_list.set_sensitive(True)
        for bar in self.progress_bars:
            bar.set_fraction(1)

        test_list_store = self.test_list.get_model()
        testfile = test_list_store[0]
        if not testfile[1]:
            open_notify_gui(self.context, "First test, no result\nNo Devices?\nHW failure?")
            testfile[1] = self.context.get_pass_fail_icon_name(False)

        self.update_status_time()
        self._stop_update()

        self.update_info_status(self.last_test_result)
        self.update_info_status_icon(self.last_test_result)

    def select_testfile(self, select_testfile):
        test_list = self.test_list
        selector = test_list.get_selection()
        test_list_store = test_list.get_model()
        for testfile in test_list_store:
            if testfile[0] == select_testfile:
                selector.select_iter(testfile.iter)
                test_list.scroll_to_cell(
                    test_list_store.get_path(testfile.iter),
                    None, False, 0, 0)
                self.out_buf.set_text("")
                break
        self.update_info_status()
        self.update_info_status_icon()

    def start_outfile(self, outfile):
        self.test_time = time.time()
        self.out_text.get_buffer().set_text("")

    def start_logfile(self, logfile):
        self.log_text.get_buffer().set_text("")

    def test_status(self, args):
        passfail = args.split(' ')[0]
        passfail = passfail == "True"
        self.last_test_result = passfail
        test_list = self.test_list
        selection = test_list.get_selection()
        test_list_store = test_list.get_model()
        treeiters = selection.get_selected_rows()[1]
        for treeiter in treeiters:
            test_list_store[treeiter][1] = self.context.get_pass_fail_icon_name(passfail)
        self.test_time = 0
        self.info_status_spinner.start()
        self.update_info_status()

    def freeze(self):
        self.unfreeze_btn.show_all()
        self.info_status_spinner.stop()

    def on_unfreeze(self):
        self.unfreeze_btn.hide()
        self.run_group_man.unfreeze()
        self.info_status_spinner.start()

    def _stop_update(self):
        if not self.update_id is None:
            GLib.source_remove(self.update_id)
            self.update_id = None

    def go_back(self):
        self._stop_update()
        self.total_duration = None
        self.context.pop_view()

    def on_ok(self):
        r = self.run_group_man.submit()
        self.go_back()
        if not r:
            open_notify_gui(self.context, "Submitting result to DB failed")

    def force_stop(self):
        self.unfreeze_btn.hide()
        self.run_group_man.stop()
        self.finished()
        self._stop_update()

    def on_cancel(self):
        if self.run_group_man.live:
            self.force_stop()
            self.test_list.set_sensitive(True)
            self.run_group_man.readonly = True
        else:
            # If test finished, cancel is same as ok.
            self.on_ok()

    def update_run_lab(self, stamp=None):
        """ Update the top title label with timestamp """
        tests_group = self.context.tests_group

        line = tests_group.name

        if tests_group.note:
            line += f" ({tests_group.note})"

        if stamp:
            if isinstance(stamp, tuple):
                line += "- %02u:%02u:%02u.%02u" % stamp
            else:
                line = f'"{line}"\n @ {str(stamp)}'

        for label in self.run_labels:
            label.set_text(line)

    def on_redo(self):
        self.force_stop()
        # Submit results, which will only happen if tests finished.
        self.run_group_man.submit()
        # Clean files would happen in submit, but only if tests finished.
        self.run_group_man.clean_files()
        self.start_test_group()
        self.update_run_lab()

    def on_info(self):
        self.context.change_view("TestGroupRunnerInfo")

    def on_back(self):
        self.context.change_view("TestGroupRunnerMain")
        self.unfreeze_btn.set_visible(self.run_group_man.is_frozen())

    def update_info_status(self, passfail=None):
        """ Updates the test info label on the logger window """
        if self.run_group_man.current_test is not None:
            self.current_test = self.run_group_man.current_test
            current_test_number = 1 + self.tests.index(self.current_test)
            self.info_status_label.set_text(f"({current_test_number}/{len(self.tests)}) {self.current_test}")
            self.info_status_spinner.start()
        else:
            self.info_status_label.set_text(f"(0/{len(self.tests)})")

    def update_info_status_icon(self, passfail=None):
        """ If passfail is none, reinstate the spinner,
        else change the icon to tick or cross """
        if passfail is None:
            to_remove = self.info_status_icon
            to_add = self.info_status_spinner
            self.info_status_spinner.start()
        else:
            if passfail:
                icon = self.context.good_icon
            else:
                icon = self.context.bad_icon
            self.info_status_icon.set_from_pixbuf(icon)
            to_remove = self.info_status_spinner
            to_add = self.info_status_icon

        to_remove.hide()
        to_add.show()


    def _run(self):
        self.run_ok_btn.set_sensitive(False)
        self.test_list.set_sensitive(False)

        self.out_buf.set_text("")
        context = self.context
        tests_group = context.tests_group

        for bar in self.progress_bars:
            bar.set_fraction(0)
        if tests_group.duration:
            self.total_duration = tests_group.duration * len(context.devices)
        else:
            self.total_duration = None
        if self.update_id is None:
            self.update_id = GLib.timeout_add(250, self.update_progress)

        self.last_test_result = False
        self.test_time = 0
        self.total_test_time = 0
        self.test_start_time = time.time()
        if not self.run_group_man.start():
            self.finished()


    def start_test_group(self):
        test_list_store = self.test_list.get_model()
        test_list_store.clear()

        for test in self.context.tests_group.tests:
            test_list_store.append([test.name, None, test])

        self._run()

    def _scroll_to_end(self, widget, alloc):
        adj = widget.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def _log_line(self, line, tag):
        self.log_buf.insert_with_tags(self.log_buf.get_end_iter(),
                                      line, tag)

    def _error_line(self, line):
        self._log_line(line, self.err_tag)

    def _warning_line(self, line):
        self._log_line(line, self.warn_tag)

    def _info_line(self, line):
        self._log_line(line, self.info_tag)


    def _out_line(self, line, tag):
        self.out_buf.insert_with_tags(self.out_buf.get_end_iter(), line, tag)

    def _good_line(self, line):
        self._out_line(line[6:], self.good_tag)

    def _bad_line(self, line):
        self._out_line(line[5:], self.bad_tag)

    def _normal_line(self, line):
        self._out_line(line, self.norm_tag)


    def update_progress(self):
        if self.total_duration and self.test_time:
            now = time.time()
            self.total_test_time += now - self.test_time
            self.test_time = now

            fraction = self.total_test_time / self.total_duration

            for bar in self.progress_bars:
                bar.set_fraction(fraction)

        self.update_status_time()

        return True

    def select_dev(self, dev_uuid):
        self.current_dev = dev_uuid

    def load_info(self):
        if not self.run_group_man.readonly:
            return
        test_sel = self.test_list.get_selection()

        test_model, test_iters = test_sel.get_selected_rows()

        self.log_text.get_buffer().set_text("")
        self.out_text.get_buffer().set_text("")

        if len(test_iters) and self.current_dev is not None:
            self.current_test = test_model[test_iters[0]][0]
            dev_result = self.run_group_man.session_results.get(self.current_dev, None)
            if not dev_result:
                self.current_dev, dev_result = list(self.run_group_man.session_results.items())[0]
            if self.current_dev:
                pass_fail = dev_result['tests'][self.current_test].get("passfail")
                self.run_group_man.load_files(self.current_dev, self.current_test)
                self.update_info_status(pass_fail)
                self.update_info_status_icon(pass_fail)


    def load_session(self, session):
        self.current_dev = session.devs_uuid[0]
        self.current_test = None
        dev_result = session.devices[self.current_dev]
        test_list_store = self.test_list.get_model()
        test_list_store.clear()
        self.tests = []
        for test_result in dev_result.results:
            name = test_result[1]
            icon = self.context.get_pass_fail_icon_name(test_result[0])
            test_list_store.append([name, icon, None])
            self.tests += [name]

        for bar in self.progress_bars:
            bar.set_fraction(1)
        self.test_list.set_sensitive(True)
        self.run_group_man.load_session(session)
        self.info_status_label.set_text(f"No test selected")
        self.update_info_status_icon(dev_result.pass_fail)


    def set_run_ready(self):
        self.tests = [ test_obj.name for test_obj in
                       self.context.tests_group.tests ]
        self.run_group_man.readonly = False


def set_run_context_singleton(_custom_run_context):
    global _run_context
    _run_context = _custom_run_context


def open_run_group(context):

    global _run_context

    _run_context.set_run_ready()

    context.push_view()
    context.change_view("RunGroupViewObj")

    _run_context.update_run_lab()


def open_ran_group(context, session):

    global _run_context

    _run_context.load_session(session)

    context.push_view()
    context.change_view("RunGroupViewObj")

    stamp = datetime.datetime.fromtimestamp(session.time_of_tests)

    _run_context.update_run_lab(stamp)


def base_init_run_group(context, run_context):

    builder = context.builder

    global _run_context

    _run_context = run_context(context)
