import os
import sys
import time
import fcntl
import shutil
import select
import tempfile
import datetime
import traceback
import pickle

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

from multiprocessing import Process, Queue

from . import base
from . import db_values
from .py_log import dt_py_log_hook_init
from .db_common import db_std_str
from . import int_logging

def execfile(test_file, args):
    with open(test_file) as f:
        s = f.read()
        if s[0].encode() == b'\xef\xbb\xbf':
            # Argh, BOM, kill it with fire
            s = s[1:]
        exec(s, args)

get_monotonic = time.monotonic


_IPC_CMD = b"IPC_CMD:"
_IPC_TIMEOUT = 1


class ForceExitException(Exception):
    """Raise an exception exit is forced."""
    def __init__(self, exit_code, error_text="Forced Exit"):
        super().__init__()
        self.error_code = exit_code
        self.error_text = error_text


class EarlyExitException(Exception):
    """Raise an exception early exit is requested."""


class test_desc_base:
    def get_text(self, passfail, args):
        raise NotImplemented

    def get_error_no(self):
        raise NotImplemented


def crash_lines_to_log(line_logger):
    lines = traceback.format_exc().splitlines()
    for line in lines:
        line_logger(line)


class basic_test_desc(test_desc_base):
    def __init__(self, error_no, desc):
        self.desc = desc
        self.error_no = error_no

    def get_text(self, passfail, args):
        if isinstance(self.desc, tuple):
            pass_desc = self.desc[0]
            fail_desc = self.desc[1]
            desc = pass_desc if passfail else fail_desc
        else:
            desc = self.desc

        args["desc"] = desc
        args["statement"] = "is" if passfail else "is not"

        if "margin" in args:
            msg = "{desc} - ({sbj}{unit} {statement} {ref}{unit} +/- {margin}{unit})".format(**args)
        elif "ref" in args:
            msg = "{desc} - ({sbj} {statement} {ref})".format(**args)
        else:
            msg = desc

        if passfail:
            return msg + " - passed"
        else:
            return msg + " - FAILED"

    def get_error_no(self):
        return self.error_no


class base_run_group_context(object):
    def __init__(self, context, bus, last_end_time, stdout_out,
                 tmp_dir):
        tests_group = context.tests_group
        self.global_args = context.args
        self.bus = bus
        self.tests = [(test.id, test.get_file_to_local(),
                       test.name,
                       test.run_arguments)\
                      for test in tests_group.tests]
        self.stdout_out = stdout_out
        self.test_group = tests_group.name
        self.devices = []
        self.last_end_time = last_end_time
        self.tmp_dir = tmp_dir
        self.sub_test_count = 0
        self.input_queue = Queue()

    def send_cmd(self, line):
        self.stdout_out.write(_IPC_CMD + line.encode() + b"\n")
        self.stdout_out.flush()

    def get_ready_devices(self, bus_con):
        raise NotImplementedError

    def stop_devices(self):
        raise NotImplementedError

    def finished(self, bus_con):
        self.send_cmd("FINISHED")

    def freeze(self):
        self.send_cmd("FREEZE")
        self.input_queue.get()
        self.unfrozen()

    def unfrozen(self):
        pass

    def get_bus(self):
        return self.bus

    def forced_exit(self, exit_code=-1):
        if exit_code:
            raise ForceExitException(exit_code)
        else:
            raise EarlyExitException

    def _complete_check(self, args, passfail, msg):
        if not passfail:
            self.store_value("SUB_FAIL_%u" % self.sub_test_count, msg)
            if self.global_args.get("freeze_on_fail", False):
                self.lib_inf.output_normal(">>>>FROZEN UNTIL USER CONTINUES<<<<")
                self.freeze()

            if args.get("exit_on_fail", False):
                self.forced_exit()
        self.sub_test_count += 1


    def do_error_code(self, post_fix, error_num, error_text):
        self.store_value("SUB_FAIL_CODE_%s" % post_fix, error_num)
        self.lib_inf.output_bad(f"[ERROR CODE: {error_num}] {error_text}")


    def _error_code_process(self, test_name, args, results, passfail, desc, **check_args):
        error_num = desc.get_error_no()
        error_text = desc.get_text(passfail, check_args)

        if passfail:
            self.lib_inf.output_good(error_text)
        else:
            if test_name is not None: # This could be called by a child class, without a current test, like by script_crash
                results[test_name] = False
            self.do_error_code(str(self.sub_test_count), error_num, error_text)

        self._complete_check(args, passfail, error_text)

    def test_check(self, test_name, args, results, result, desc):
        if isinstance(desc, test_desc_base):
            return self._error_code_process(test_name, args, results, result, desc)

        ret = False
        desc = db_std_str(desc)

        if result:
            msg = "%s - passed" % desc
            self.lib_inf.output_good(msg)
            ret = True
        else:
            results[test_name] = False
            msg = "%s - FAILED" % desc
            self.lib_inf.output_bad(msg)
        self._complete_check(args, result, msg)

        return ret

    def threshold_check(self, test_name, args, results, sbj, ref, margin, unit, desc):
        margin = abs(margin)
        passfail = abs(sbj - ref) <= margin
        if isinstance(desc, test_desc_base):
            return self._error_code_process(test_name, args, results, passfail, desc, sbj=sbj, ref=ref, margin=margin, unit=unit)
        unit = db_std_str(unit)
        desc = db_std_str(desc)
        return self.test_check(test_name, args, results, passfail, "%s %g%s is %g%s +/- %g%s" % (desc, sbj, unit, ref, unit, margin, unit))

    def exact_check(self, test_name, args, results, sbj ,ref, desc):
        passfail = sbj == ref
        if isinstance(desc, test_desc_base):
            return self._error_code_process(test_name, args, results, passfail, desc, sbj=sbj, ref=ref)
        desc = db_std_str(desc)
        statement = "is" if passfail else "is not"
        msg = f"{desc} ({sbj} {statement} {ref})"
        return self.test_check(test_name, args, results, passfail, msg)

    def store_value(self, n, v):
        data = pickle.dumps((n, v)).replace(b"\n",b"<NL>") # Base64 includes a newline
        # DIY the the IPC as no point going in and out of utf8 on Py3
        self.stdout_out.write(_IPC_CMD)
        self.stdout_out.write(b"STORE_VALUE ")
        self.stdout_out.write(data)
        self.stdout_out.write(b"\n")
        self.stdout_out.flush()

    def script_crash(self, filename=None):
        pass

    def sleep(self, seconds, silent=False):
        if not silent:
            self.lib_inf.output_normal("Sleeping for %G seconds" % seconds)
        time.sleep(seconds)


def _thread_test(test_context):

    dt_py_log_hook_init()

    lib_inf = test_context.lib_inf

    info_enabled = lib_inf.info_msgs_is_enabled()

    bus = test_context.get_bus()

    test_context.send_cmd("START_TESTS")

    # Don't send this message to the detailed log ever, only causes issue with the command line rendering.
    lib_inf.enable_info_msgs(False)
    lib_inf.output_normal("Starting test group: " + test_context.test_group)
    lib_inf.enable_info_msgs(info_enabled)

    # Set messages to go over ICP
    lib_inf.set_log_file(test_context.stdout_out)
    lib_inf.set_output(test_context.stdout_out)

    exec_map = {'exit': test_context.forced_exit,
                'output_normal' : lib_inf.output_normal,
                'output_good' : lib_inf.output_good,
                'output_bad' : lib_inf.output_bad,
                'error_msg': lib_inf.error_msg,
                'warning_msg' : lib_inf.warning_msg,
                'info_msg' : lib_inf.info_msg,
                'freeze_test' : test_context.freeze,
                "sleep" : test_context.sleep,
                }

    try:
        bus_con = bus.open()
    except Exception as e:
        lib_inf.error_msg(f"Bus open failed: {str(e)}")
        crash_lines_to_log(lib_inf.error_msg)
        test_context.script_crash()
        bus_con = None

    if bus_con:
        try:
            ready_devices = test_context.get_ready_devices(bus_con)
        except Exception as e:
            lib_inf.error_msg(f"Get devices failed : {str(e)}")
            crash_lines_to_log(lib_inf.error_msg)
            test_context.script_crash()
            ready_devices = []

        if not len(ready_devices):
            lib_inf.error_msg("No devices")

        full_stop=False

        # Don't send the test announcement ever to info log
        lib_inf.enable_info_msgs(False)

        for dev in ready_devices:
            results = {}

            dev_pass = True

            dev_uuid = dev.uuid.rstrip('\0')

            test_context.send_cmd("SELECT_DEV " + dev_uuid)

            for test in test_context.tests:

                test_id   = test[0]
                test_file = test[1]
                name      = test[2]
                args      = test[3]
                test_context.send_cmd("SELECT_TEST " + name)

                line = "Testing device: '%s' Test: '%s' of Group: '%s'"\
                        % (dev_uuid, name, test_context.test_group)

                stamp = datetime.datetime.utcnow()

                entry_name = "%s_%s_%s_%s" % (test_context.test_group,
                                              dev_uuid,
                                              name,
                                              str(stamp))
                entry_name = entry_name.replace('/', '_')

                log_path = os.path.join(test_context.tmp_dir,
                                        "%s.log" % entry_name)
                output_path = os.path.join(test_context.tmp_dir,
                                            "%s.output" % entry_name)

                test_context.send_cmd("START_OUTPUT %s" % output_path)
                test_context.send_cmd("START_LOGFILE %s" % log_path)

                duration = 0

                lib_inf.output_normal("=" * len(line))
                lib_inf.output_normal(line)
                lib_inf.output_normal("=" * len(line))

                results[name] = True

                test_check      = lambda a,b:       test_context.test_check(name, args, results, a, b)
                threshold_check = lambda a,b,c,d,e: test_context.threshold_check(name, args, results, a, b, c, d, e)
                exact_check     = lambda a,b,c:     test_context.exact_check(name, args, results, a, b, c)
                store_value     = lambda n, v :     test_context.store_value(n, v)

                test_context.sub_test_count = 0

                try:
                    start_time = time.time()
                    lib_inf.enable_info_msgs(True)
                    test_exec_map = exec_map.copy()
                    test_exec_map.update({ 'args': args,
                                          'dev': dev,
                                          'name': name,
                                          'test_check': test_check,
                                          'threshold_check' : threshold_check,
                                          'exact_check' : exact_check,
                                          'results': results,
                                          'store_value' : store_value,
                                          '__file__' : os.path.abspath(test_file)})
                    if hasattr(dev, "start_test"):
                        dev.start_test(name)
                    if hasattr(dev, "set_test_functions"):
                        dev.set_test_functions(test_check, threshold_check, exact_check, store_value)
                    bus_con.poll_devices()
                    execfile(test_file, test_exec_map)
                    duration = time.time() - start_time
                except ForceExitException as e:
                    duration = time.time() - start_time
                    results[name] = False
                    lib_inf.output_bad("Forced Exit")
                    test_context.do_error_code('N', e.error_code, e.error_text)
                    store_value("SUB_FAIL_N", e.error_text)
                    full_stop = True
                except EarlyExitException:
                    duration = time.time() - start_time
                except Exception as e:
                    duration = time.time() - start_time
                    results[name] = False
                    store_value("SUB_FAIL_N", "SCRIPT CRASH")
                    lib_inf.error_msg("Exception:")
                    for line in str(e).splitlines():
                        lib_inf.error_msg(line)
                    lib_inf.error_msg("Backtrace:")
                    crash_lines_to_log(lib_inf.error_msg)
                    test_context.script_crash(name)
                    full_stop = True

                post_dev_uuid = dev.uuid.rstrip('\0')
                if dev_uuid != post_dev_uuid:
                    test_context.send_cmd("SET_UUID %s" % post_dev_uuid)
                    dev_uuid = post_dev_uuid
                lib_inf.enable_info_msgs(False)

                test_pass = results.get(name, False)

                dev_pass &= test_pass

                test_context.send_cmd("STATUS_TEST %s %G" % (str(bool(test_pass)), duration))

                if not test_pass and args.get('exit_on_fail', False):
                    full_stop = True

                if full_stop:
                    break

            test_context.send_cmd("STATUS_DEV %s" % str(bool(dev_pass)))

            if full_stop:
                break
    try:
        test_context.finished(bus_con)
    except Exception as e:
        base_run_group_context.finished(test_context, None)
        lib_inf.error_msg(f"Failed to finish with overloaded context.finish : {str(e)}")
        crash_lines_to_log(lib_inf.error_msg)
        test_context.script_crash()
    try:
        bus.close()
    except Exception as e:
        lib_inf.error_msg(f"Bus close failed : {str(e)}")
        crash_lines_to_log(lib_inf.error_msg)
        test_context.script_crash()


class base_run_group_manager(object):
    def __init__(self, context,
                 _run_group_context_class,
                 good_line = None,
                 bad_line = None,
                 normal_line = None,
                 info_line = None,
                 warning_line = None,
                 error_line = None,
                 cmds = None):

        self._logger = int_logging.get_logger(__name__)

        self.process = None
        self.test_context = None
        self.last_end_time = 0
        self.context = context
        self._run_group_context_class = _run_group_context_class
        stdout_in, stdout_out = os.pipe()

        self.session_results = {}
        self.current_test = None
        self.current_device = None

        self.stdout_out = os.fdopen(stdout_out, "wb", 0)
        self.stdout_in = os.fdopen(stdout_in, "rb", 0)
        fcntl.fcntl(self.stdout_in.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)

        self.live = False
        self.readonly = False
        self.frozen = False

        self.outfile = None
        self.logfile = None
        self._pre_logfile = None
        self.files = []

        no_op = lambda x: None

        self.good_line    = good_line if good_line else no_op
        self.bad_line     = bad_line if bad_line else no_op
        self.normal_line  = normal_line if normal_line else no_op

        self.info_line    = info_line if info_line else no_op
        self.warning_line = warning_line if warning_line else no_op
        self.error_line   = error_line if error_line else no_op

        self.has_new = False

        self.external_cmds = cmds if cmds else {}

        self.cmds = {"FINISHED":      lambda args:     self._finished(),
                     "SELECT_TEST":   lambda testfile: self._select_testfile(testfile),
                     "SELECT_DEV" :   lambda dev_uuid: self._select_dev(dev_uuid),
                     "START_OUTPUT":  lambda outfile:  self._start_outfile(outfile),
                     "START_LOGFILE": lambda logfile:  self._start_logfile(logfile),
                     "STATUS_TEST":   lambda args:     self._test_status(args),
                     "STATUS_DEV":    lambda passfail: self._dev_status(passfail == "True"),
                     "SET_UUID":      lambda new_uuid: self._dev_set_uuid(new_uuid),
                     "STORE_VALUE":   lambda data:     self._store_value_picked(data),
                     "FREEZE":        lambda args:     self._freeze(),
                     }

        GLib.io_add_watch(self.stdout_in,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                          self._stdout_in_event)


    def _stdout_in_event(self, src, cond):
        # DIY readline with timeout
        now = get_monotonic()
        end_time = now + _IPC_TIMEOUT
        line = b""
        timeout=True
        while now < end_time:
            try:
                c = self.stdout_in.read(1)
            except IOError as e:
                c = None
            if c is None:
                try:
                    select.select([self.stdout_in],[],[], end_time-now)
                except IOError as e:
                    c = None
            elif c == b'\n':
                line += c
                timeout = False
                break
            else:
                line += c
            now = get_monotonic()
        if not line:
            return True
        if not timeout:
            try:
                return self.process_line(line)
            except Exception as e:
                self._logger.error(f"LINE PROCESS FAILED : {str(e)}")
                crash_lines_to_log(self._logger.error)
        else:
            self._logger.warning("Part line received, but timed out.")
        self.stop()
        ext_cmd = self.external_cmds.get("FINISHED",None)
        if ext_cmd:
            ext_cmd("")

    def _process_die_catch(self):
        if not self.process:
            return False
        if self.process.exitcode is None:
            return True
        if self.process.exitcode < 0:
            self._logger.info("Process terminated.")
            self.live = False
            self._complete_stop()
            self._finished()
            return False
        return True

    def _finished(self):
        if self.outfile:
            self.outfile.close()
            self.outfile = None
        if self.logfile:
            self.logfile.flush() # Write what we have, bute capture any output afterwards.

        self.current_test = None
        self.current_device = None

    def _select_testfile(self, testfile):
        self.current_test = testfile

    def _select_dev(self, dev_uuid):
        self.current_device = dev_uuid.strip()

    def _start_outfile(self, outfile):
        if self.outfile:
            self.outfile.close()
            self.outfile = None
        if len(outfile):
            self.outfile = open(outfile, "w")
            self.files += [outfile]
            self.session_results[self.current_device]['tests'][self.current_test]['outfile'] = outfile

    def _start_logfile(self, logfile):
        if not len(logfile): # We never stop logging during testing.
            return
        # If it's the first, take any log messages that was before hand.
        if self.logfile == self._pre_logfile:
            self._pre_logfile.close()
            shutil.copy(self._pre_logfile.name, logfile)
            os.unlink(self._pre_logfile.name)
            self._pre_logfile = None
            self.logfile = open(logfile, "a")
        else:
            if self.logfile:
                self.logfile.close()
            self.logfile = open(logfile, "w")
        self.files += [logfile]
        self.session_results[self.current_device]['tests'][self.current_test]['logfile'] = logfile

    def _test_status(self, args):
        args = args.split(' ')
        passfail = args[0]
        passfail = passfail == "True"
        duration = float(args[1])
        tests_results = self.session_results[self.current_device]['tests']
        if self.current_test in tests_results:
            results = tests_results[self.current_test]
            results['passfail'] = passfail
            results['duration'] = duration
        if self.outfile:
            self.outfile.close()
            self.outfile = None
        if self.logfile:
            self.logfile.flush() # Write what we have, but capture anything until the next test starts

    def _dev_status(self, passfail):
        self.session_results[self.current_device]['tests'][self.current_test]['passfail'] = passfail

    def _dev_set_uuid(self, new_uuid):
        new_uuid = new_uuid.strip()
        dev_values = self.session_results.pop(self.current_device)
        if "old_uuid" not in dev_values:
            dev_values["old_uuid"] = self.current_device
        self.session_results[new_uuid] = dev_values
        for dev in self.context.devices:
            if dev.uuid.strip() == self.current_device:
                dev.uuid = new_uuid
        self.current_device = new_uuid

    def _store_value_picked(self, data):
        data = pickle.loads(data.replace(b"<NL>",b"\n"))
        name = data[0]
        value = data[1]
        self._store_value(name, value)

    def _store_value(self, name, value):
        if not self.current_device or not self.current_test:
            context = self.context
            # If this is an error code, this could be useful in the DB.
            if not len(context.devices) or not len(self.context.tests_group.tests):
                return
            # These are DB devices, but the UUID and order should be the same.

            # Ok, so this is before/after the tests are run, so put in for first device
            current_device = context.devices[0].uuid

            # Use last test that ran
            tests_dict = self.session_results[current_device]['tests']
            current_test = None
            for test in self.context.tests_group.tests:
                if tests_dict[test.name]['logfile']:
                    current_test = test.name
                else:
                    break
            if not current_test:
                # Just use first test
                current_test = self.context.tests_group.tests[0].name
        else:
            current_device = self.current_device
            current_test = self.current_test
        test_dict = self.session_results[current_device]['tests'][current_test]
        test_dict.setdefault("stored_values", {})
        test_dict["stored_values"][name] = value

    def _freeze(self):
        self.frozen = True

    def unfreeze(self):
        self.test_context.input_queue.put(True)
        self.frozen = False

    def is_frozen(self):
        return self.frozen and self.live

    def process_line(self, line):
        def line_is_log(s):
            return len(s) > 22 and \
               s[2] == '/' and \
               s[5] == ' ' and \
               s[8] == ':' and \
               s[22] == '['
        def process_log_line(l):
            try:
                #23 = len("25/01 10:51:47.241291 [")
                s = l.index(' ', 24)
                s += 1
                e = l.index(':', s)
                payload = l[e + 2:]
                token = l[s:e]
            except:
                token = ""
            if token == "ERROR":
                self.error_line(l)
                self._logger.error(payload)
            elif token == "WARN":
                self.warning_line(l)
            else:
                self.info_line(l)

            if self.logfile:
                self.logfile.write(l)

        if not self.live:
            if isinstance(line, bytes) and line.startswith(_IPC_CMD + b"START_TESTS"):
                self.live = True
            if self.logfile:
                if isinstance(line, bytes):
                    line = line.decode(errors='replace')
                if line_is_log(line):
                    process_log_line(line)
            return True
        if isinstance(line, bytes) and line.startswith(_IPC_CMD):
            line = line[len(_IPC_CMD):].strip()
            parts = line.split(b' ')
            name = parts[0]
            opt = b" ".join(parts[1:]) if len(parts) > 1 else None
            name = db_std_str(name)
            if name != "STORE_VALUE":
                opt = db_std_str(opt)
            cb = self.cmds.get(name, None)
            if cb:
                cb(opt)
            cb = self.external_cmds.get(name, None)
            if cb:
                cb(opt)
        else:
            if isinstance(line, bytes):
                line = line.decode(errors='replace')
            if line_is_log(line):
                process_log_line(line)
            else:
                if line.startswith("Good: "):
                    self.good_line(line)
                elif line.startswith("BAD: "):
                    self.bad_line(line)
                else:
                    self.normal_line(line)

                if self.outfile:
                    self.outfile.write(line)

        return True

    def start(self):
        lib_inf = self._run_group_context_class.lib_inf
        if self.process:
            return False
        bus = self.context.lock_bus()
        if not bus:
            return False

        try:
            self.readonly = False
            self.live = False
            self.has_new = True

            self.session_results = {}

            if self._pre_logfile:
                self._pre_logfile.close()
                os.unlink(self._pre_logfile.name) # Just to be sure

            self._pre_logfile = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) # Place to put errors before tests start

            self.logfile = self._pre_logfile

            test_results = {'tests' :
                            dict([( test.name, {'passfail' : False, 'logfile' : None } )
                            for test in self.context.tests_group.tests])}

            for dev in self.context.devices:
                self.session_results[dev.uuid.strip()] = test_results

            self.test_context = self._run_group_context_class(self.context, bus,
                                                              self.last_end_time,
                                                              self.stdout_out)
            self.process = Process(target=_thread_test,
                                  args=(self.test_context,))
            GLib.timeout_add_seconds(1, self._process_die_catch)
            self.frozen = False
            self.process.start()
            return True

        except Exception as e:
            msg = f"BAD: FAILED to start: {str(e)}\n"
            lib_inf.error_msg("Backtrace:")
            crash_lines_to_log(lib_inf.error_msg)
            self.stop()
            self.live = True
            self.process_line(msg)
            self.live = False
            self.context.release_bus()
            return False

    def _clean_after_process(self):
        self.process = None
        self.last_end_time = time.time()
        # Drain IPC
        r = select.select([self.stdout_in],[],[], 0.01)
        while r[0]:
            self._stdout_in_event(None, None)
            r = select.select([self.stdout_in],[],[], 0.01)
        if self.logfile: # Nothing to log any more, the test process is now gone.
            self.logfile.close()
            self.logfile = None
        self.context.release_bus()
        if self._pre_logfile:
            # Didn't even get to the first test
            context = self.context
            if len(context.devices) and len(self.context.tests_group.tests):
                # These are DB devices, but the UUID and order should be the same.
                # Put these logs on to first test
                current_device = context.devices[0].uuid.rstrip('\0')
                first_test = context.tests_group.tests[0]

                stamp = datetime.datetime.utcnow()

                filename = "%s_%s_%s_%s.log" % (self.test_context.test_group,
                                              current_device,
                                              first_test.name,
                                              str(stamp))
                filename = filename.replace('/', '_')

                logfilepath = os.path.join(self.test_context.tmp_dir,
                                       filename)

                self._pre_logfile.close()
                shutil.copy(self._pre_logfile.name, logfilepath)
                os.unlink(self._pre_logfile.name)
                self._pre_logfile = None

                tests_dict = self.session_results[current_device]['tests'][first_test.name]
                self.files += [logfilepath]
                tests_dict['logfile'] = logfilepath


    def wait_for_end(self):
        if self.process:
            self.live = False
            self.process.join(4)
            self._clean_after_process()
            self.readonly = True

    def _complete_stop(self):
        try:
            self.test_context.stop_devices()
        except Exception as e:
            self._logger.error(f"Failed to stop devices. {str(e)}")
            self._logger.error("Backtrace:")
            crash_lines_to_log(self._logger.error)

        self._clean_after_process()
        self.live = False # Should already be False, but concurrence means it could have changed before process stopped.
        self.frozen = False

    def stop(self):
        self.live = False
        if self.process:
            self.process.terminate()
            # Give time for signal to get slave process.
            time.sleep(0.1)
            self._complete_stop()
            self._store_value("SUB_FAIL_N", "SCRIPT CANCELLED")

        self._finished()

    def clean_files(self):
        for f in self.files:
            if os.path.exists(f):
                os.unlink(f)
        self.files = []
        self.has_new = False

    def load_files(self, dev, test):
        self.live = True
        if dev in self.session_results:
            dev_results = self.session_results[dev]['tests']
            if test in dev_results:
                values = dev_results[test]

                for k in [ 'outfile', 'logfile' ]:
                    f = values.get(k, None)
                    if f is None:
                        continue
                    if isinstance(f, int):
                        try:
                            local_file = self.context.db.get_file_to_local(f)
                            values[k] = local_file
                        except Exception as e:
                            self.process_line(f"BAD: Failed to load session: {str(e)}\n")
                            self.live = False
                            local_file = None
                    else:
                        local_file = f
                    if local_file and os.path.exists(local_file):
                        with open(local_file, "rb") as f:
                            for line in f:
                                self.process_line(line)
        self.live = False

    def load_session(self, session):
        self.has_new = False
        self.live = False
        self.readonly = True
        self.session_results = {}

        for uuid, dev_results in session.devices.items():
            r = {}
            for pass_fail, name, out_file_id, log_file_id, duration in dev_results.results:
                r[name] = { 'passfail': pass_fail,
                            'outfile': out_file_id,
                            'logfile': log_file_id}
            self.session_results[uuid] = {'tests' : r}


    def is_pass(self, dev=None, test=None):
        if dev is None:
            if test is None:
                if not len(self.session_results):
                    return False
                for dev_tests in self.session_results.values():
                    dev_tests = dev_tests['tests']
                    for test_results in dev_tests.values():
                        if not test_results.get('passfail',False):
                            return False
                return True
            for uuid, dev_results in self.session_results.items():
                dev_results = dev_results['tests']
                if test not in dev_results:
                    return -1
                if not dev_results[test]['passfail']:
                    return False

            return True
        if test is None:
            if dev not in self.session_results:
                return -1
            r = [ results.get('passfail', False) \
                  for results in self.session_results[dev]['tests'].values()]
            return bool(min(r))
        if dev not in self.session_results:
            return -1
        dev_results = self.session_results[dev]['tests']
        if test not in dev_results:
            return -1
        return dev_results[test]['passfail']

    def submit(self):
        if self.has_new:
            r = self.context.tests_group.add_tests_results(self.context.devices, self.session_results)
            self.clean_files()
            return r
        return True




class default_group_context(base_run_group_context):
    lib_inf = base

    def __init__(self, context, bus, last_end_time, stdout_out):
        tmp_dir="/tmp"
        base_run_group_context.__init__(self, context, bus, last_end_time, stdout_out, tmp_dir)
        self.devices = context.devices
        self._logger = int_logging.get_logger(__name__)

    def get_ready_devices(self, bus_con):
        try:
            bus_con.ready_devices(self.devices)
            return bus_con.devices
        except Exception as e:
            self._logger.error(f"Failed to get devices : {str(e)}")
            self._logger.error("Backtrace:")
            crash_lines_to_log(self._logger.error)
            return []

    def stop_devices(self):
        # Tests have been killed, so open and close the bus to ensure it's closed.
        with self.bus as bus_con:
            pass


class default_run_group_manager(base_run_group_manager):
        def __init__(self, context,
                 good_line = None,
                 bad_line = None,
                 normal_line = None,
                 info_line = None,
                 warning_line = None,
                 error_line = None,
                 cmds = None):
            base_run_group_manager.__init__(self,
                                            context,
                                            default_group_context,
                                            good_line,
                                            bad_line,
                                            normal_line,
                                            info_line,
                                            warning_line,
                                            error_line,
                                            cmds)
