import os
import sys

import example_lib
import example_lib_gui

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib

from dt_gui_base import scan_box_base, open_query_gui, open_dev_tests_sessions_results


_default_test_group = "Sunny Day"

_singleton = None


def _board_in_rig(context, db_dev, next_view, serial_number, yes_no):

    if not yes_no:
        return

    if not db_dev:
        db_dev = example_lib.db_example_dev.create(context.db, serial_number, "UNSET")
        db_dev.update_uuid("<unknown>-%u" % db_dev.id)
    context.devices = [db_dev]

    next_view(context)


def _is_board_in_rig(context, db_dev, next_view, serial_number):
    open_query_gui("Place board in test fixture.\nIs it now in the fixture?",
                           lambda yes_no: _board_in_rig(context, db_dev, next_view, serial_number, yes_no))


def _view_results(context, db_dev, next_view, serial_number, yes_no):

    if yes_no:
        context.devices = [db_dev]
        open_dev_tests_sessions_results(context, db_dev)
        return

    _is_board_in_rig(context, db_dev, next_view, serial_number)


class _start_double_scan(scan_box_base):
    def __init__(self, context):
        scan_box_base.__init__(self, context,
                               "start_scan_barcodeA_entry",
                               "start_scan_barcodeB_entry")
        self.start_scan_status_lab = context.builder.get_object("start_scan_status_lab")
        self.selected_group = None

    def reset_scan(self):
        scan_box_base.reset_scan(self)

    def barcode_do(self, serial_number):
        context = self.context

        if not context.db:
            if not context.db_init():
                self.set_status("Unable to connect to DB.")
                return

        if serial_number.startswith("freeze_on_fail:"):
            data = serial_number.replace("freeze_on_fail:", "").strip().upper()
            freeze_on_fail = data == "ON"
            context.args["freeze_on_fail"] = freeze_on_fail
            self.set_status("Freeze On Fail : %s" % ("ON" if freeze_on_fail else "OFF"))
            return

        if serial_number.startswith("arguments_override:"):
            arguments = serial_number.replace("arguments_override:", "").strip()
            try:
                arguments = json.loads(arguments)
            except:
                self.set_status("Invalid arguments")
                return

            self.context.custom_args = arguments
            self.set_status("Custom arguments set")
            return

        selected_group = context.db.get_group(serial_number)
        if selected_group:
            self.selected_group = selected_group
            self.set_status("Test group selected.")
            return

        if context.args['super']:
            next_view = example_lib_gui.open_groups_list
        else:
            if not self.selected_group:
                self.selected_group = context.db.get_group(_default_test_group)
                assert self.selected_group, "Can't find default test group to run."
            db_test_group = self.selected_group
            context.tests_group.populate_from(db_test_group)
            from .group_run_gui import open_run_group
            next_view = open_run_group

        db_dev = example_lib.db_example_dev.get_by_serial(context.db, serial_number)
        if not db_dev:
            _is_board_in_rig(context, db_dev, next_view, serial_number)
        else:
            open_query_gui("Board known, view results?",
                           lambda yes_no: _view_results(context, db_dev, next_view, serial_number, yes_no))

    def set_status(self, msg):
        self.start_scan_status_lab.set_text(msg)


class _start_singleton(object):
    def __init__(self, context):
        self.context = context
        builder = context.builder
        self.scan_view = _start_double_scan(context)
        self.scan_view.open()

        context.view_objs["StartViewObj"] = self

        self.refresh_id = GLib.timeout_add_seconds(5,
                                               lambda : self._refresh())

    def _refresh(self):
        if self.context.db_error:
            self.scan_view.set_status("Bad Database connection")
            if self.context.db_init():
                self.scan_view.set_status("Database connected")
                self.scan_view.set_enable(True)
            else:
                self.scan_view.set_enable(False)

        if self.context.db:
            self.context.db.clean()

        return True

    def show_view(self):
        self.context.force_view("Start")
        self.scan_view.reset_scan()
        self._refresh()



def open_start_page(context):
    context.change_view("StartViewObj")

def init_start_page(context):
    global _singleton
    _singleton = _start_singleton(context)
