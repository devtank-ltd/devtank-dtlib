'''

TODO: Really this should always be called a different thread than the GUI thread while the GUI thread has a waiting indicator.

'''
import os
import sys
import copy
import yaml
import uuid
import csv


from .test_file_extract import get_args_in_src
from .db_filestore_protocol import smb_transferer, sftp_transferer, tar_transferer

from .db_common import *
from .db_tests import test_script_obj, test_group_obj, test_group_sessions

from .db_values import value_obj_t
from .tests_group import tests_group_creator
from .db_tester import db_tester_machine


class tester_database(object):
    def __init__(self, db, sql, work_folder, db_def={}):
        self.db = db
        self.sql = sql
        sql.setup(db)
        self.version = sql.db_version

        self.work_folder = work_folder
        self._known_objs = {}
        self.protocol_transferers = {sftp_transferer.protocol_id : sftp_transferer(db_def),
                                     smb_transferer.protocol_id  : smb_transferer(),
                                     tar_transferer.protocol_id : tar_transferer(self, sql, work_folder) }
        self._file_upload_cache = (None, {})
        self._new_tests_cache = (None, {})
        if not os.path.exists(work_folder):
            os.mkdir(work_folder)
        self.tester_machine = None
        self.settings = value_obj_t.get_settings_root(self)
        self.test_props = value_obj_t.get_test_props_root(self)
        self.result_values = value_obj_t.get_result_props_root(self)
        self.props_defaults = self.settings.get_child("defaults")

    def clean(self):
        for protocol_transferer in self.protocol_transferers.values():
            protocol_transferer.clean()
        self.db.clean()

    def get_filestore_protocol_id(self, protocol_name):
        cmd = self.sql.get_file_store_protocol_id(protocol_name)
        row = self.db.query_one(cmd)
        if row is None:
            return None
        return row[0]

    def add_filestore(self, host, folder, writable, protocol_id):
        host = host.upper()
        db = self.db
        sql = self.sql

        self.protocol_transferers[protocol_id].init(host, folder)

        c = db.cursor()
        found = False

        rows = c.query(sql.get_all_file_stores())
        for row in rows:
            if row[1].upper() == host:
                if row[2] == folder and row[3] == protocol_id:
                    rw = True if row[4] else False
                    writable = True if writable else False
                    if rw != writable:
                        c.update(sql.update_file_store_writable(row[0], 1 if writable else 0))
                    found = True
                    break

        if not found:
            c.insert(sql.add_file_store(host, folder, 1 if writable else 0, protocol_id ))
        db.commit()

    def add_filestore_protocol(self, name):
        db = self.db
        sql = self.sql
        c = db.cursor()

        protocol_id = c.query(sql.get_file_store_protocol_id(name))
        if protocol_id:
            return

        protocol_id = c.insert(sql.add_file_store_protocol(name))
        db.commit()

    def _new_test_group(self, db_id, name, desc, query_time=None):
        return test_group_obj(self, db_id, name, desc, query_time=query_time)

    def _new_test_obj(self, *args):
        return test_script_obj(self, *args)

    def get_all_tests(self, now=None):
        if now is None:
            now = db_ms_now()
        rows = self.db.query(self.sql.get_all_tests(now))
        return [ self._new_test_obj( row[0], row[1], row[2]) for row in rows ]

    def get_deleted_tests(self):
        now = db_ms_now()
        rows = self.db.query(self.sql.get_deleted_tests(now))
        return [ self._new_test_obj( row[0], row[1], row[2]) for row in rows ]

    def get_groups(self, now=None):
        if now is None:
            now = db_ms_now()
        rows = self.db.query(self.sql.get_groups(now))
        return [self._new_test_group(row[0], row[1], row[2]) for row in rows ]

    def get_group_by_id(self, group_id, now=None):
        if now is None:
            now = db_ms_now()
        cmd = self.sql.get_group_by_id(group_id)
        rows = self.db.query(cmd)
        if not len(rows):
            return None
        row = rows[0]
        return self._new_test_group( row[0], row[1], row[2], query_time=now)

    def get_group(self, group_name, now=None):
        if now is None:
            now = db_ms_now()
        cmd = self.sql.get_group_by_name(group_name, now)
        rows = self.db.query(cmd)
        if not len(rows):
            return None
        row = rows[0]
        return self._new_test_group(row[0], row[1], row[2])

    def _add_files(self, c, filepaths, now=None):
        file_store = c.query_one(self.sql.get_rw_file_store())
        file_store_id = file_store[0]
        file_store_host = file_store[1]
        file_store_folder = file_store[2]
        protocol_id = file_store[3]
        if protocol_id not in self.protocol_transferers:
            raise Exception("Unknown protocol for filestore.")

        protocol_transferer = self.protocol_transferers[protocol_id]

        if now is None:
            now = db_ms_now()

        tar_vstore_row = self.get_tar_virtual_filestore()

        id_cache = self._file_upload_cache[1]
        if self._file_upload_cache[0] != c:
            id_cache = {}
            self._file_upload_cache = (c, id_cache)

        fresh_files_to_upload = set()

        # Boil down to unique files that will be acturally added.
        for filepath in filepaths:
            if filepath not in id_cache:
                fresh_files_to_upload.add(filepath)

        if len(fresh_files_to_upload) > 1 and tar_vstore_row:

            filestore_protocol_transferer = protocol_transferer

            protocol_id = tar_transferer.protocol_id
            protocol_transferer = self.protocol_transferers[protocol_id]

            filename = protocol_transferer.start_tar(c)

            mod_time = db_time(time.time())
            file_size = 0

            completed_tar = filename
            completed_tar_id = c.insert(self.sql.add_file(os.path.basename(filename),
                    file_store_id, now, mod_time, file_size))

            protocol_transferer.set_tar_db_id(completed_tar_id)

            file_store_id = tar_vstore_row[0]
        else:
            protocol_transferer.open(file_store_host, file_store_folder)

        for filepath in fresh_files_to_upload:
            filename = os.path.basename(filepath)
            stat = os.stat(filepath)
            mod_time = db_time(stat.st_mtime)
            file_size = stat.st_size
            file_id = c.insert(self.sql.add_file(filename,
                file_store_id, now, mod_time, file_size))
            if not file_id:
                raise Exception("Adding file \"%s\" failed" % filename)
            protocol_transferer.upload(filepath, file_id)
            id_cache[filepath] = file_id

        if protocol_id == tar_transferer.protocol_id:
            protocol_transferer.finish_tar()
            filestore_protocol_transferer.open(file_store_host, file_store_folder)
            filestore_protocol_transferer.upload(completed_tar, completed_tar_id)
            stat = os.stat(completed_tar)
            mod_time = db_time(stat.st_mtime)
            file_size = stat.st_size
            cmd = self.sql.complete_tar_file(completed_tar_id, mod_time, file_size)
            c.update(cmd)

        # Return id array in same order as original files array.
        r = []
        for filepath in filepaths:
            file_id = id_cache.get(filepath, None)
            if file_id:
                r += [ file_id ]
        return r

    def add_files(self, filepaths, db_cursor=None, now=None):
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor
        r = self._add_files(c, filepaths, now)
        if db_cursor is None:
            db.commit()
        return r

    def get_file_to_local(self, file_id):
        if isinstance(file_id, tuple):
            file_id = file_id[-1]

        row = self.db.query_one(self.sql.get_file_filestore(file_id))
        file_store_host = row[0]
        file_store_protocol_id = row[1]
        file_store_folder = row[2]
        name = row[3]
        mod_time = row[4]
        file_size = row[5]
        local_folder = os.path.join(self.work_folder, str(file_id))
        local_file = os.path.join(local_folder, name)
        if os.path.exists(local_file):
            stat = os.stat(local_file)
            local_mod_time = db_time(stat.st_mtime)
            local_file_size = stat.st_size
            time_delta = abs(local_mod_time - mod_time)
            # Allow 1 millisecond of difference due to float/int and filesystem storage.
            if time_delta < 2 and local_file_size == file_size:
                return local_file
        elif not os.path.exists(local_folder):
            os.mkdir(local_folder)

        if file_store_protocol_id not in self.protocol_transferers:
            raise Exception("Unknown protocol for filestore.")

        protocol_transferer = self.protocol_transferers[file_store_protocol_id]

        protocol_transferer.open(file_store_host, file_store_folder)

        mod_time = db2py_time(mod_time)

        protocol_transferer.download(local_file, file_id, mod_time)

        return local_file

    def get_resource_files(self):
        rows = self.db.query(self.sql.get_resource_files())
        return dict([ row for row in rows ])

    def get_filename(self, file_id):
        row = self.db.query_one(self.sql.get_filename(file_id))
        if row is None:
            return None
        return row[0]

    def get_file_id_by_name(self, name, db_cursor=None):
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor
        row = c.query_one(self.sql.get_file_by_name(name))
        if row is None:
            return None
        return row[0]

    @staticmethod
    def _validate_args_definitions(args):
        for prop, prop_values in args.items():
            assert isinstance(prop, str), "Arg name not string"
            used = ['type', 'desc']
            prop_type = prop_values.get('type', None)
            assert prop_type is not None, "Arg has no type"
            if isinstance(prop_type, type):
                prop_pytype = prop_type
                prop_type = db_type_from_py_type(prop_type)
                assert prop_type is not None, "Arg has unknown type"
            else:
                prop_pytype = py_type_from_db_type(prop_type)
                assert prop_pytype is not None, "Arg has unknown type"
            desc = prop_values.get('desc', None)
            assert isinstance(desc, str), "Arg desc not string"

            default_value = prop_values.get('value', ValueError)
            if default_value is not ValueError:
                prop_values['default'] = prop_values.pop('value')
            else:
                default_value = prop_values.get('default', ValueError)

            if default_value is not ValueError:
                if prop_type == "float":
                    assert isinstance(default_value, float) or isinstance(default_value, int), "Arg default not of own type"
                elif prop_type == "bool":
                    assert isinstance(default_value, bool) or isinstance(default_value, int), "Arg default not of own type"
                    prop_values['default'] = bool(default_value)
                else:
                    assert isinstance(default_value, prop_pytype), "Arg default not of own type"
                used += ['default']

            if prop_type == "int" or prop_type == "float":
                for param in ["min", "max", "step"]:
                    v = prop_values.get(param, None)
                    if prop_type == "float":
                        assert isinstance(v, float) or isinstance(v, int), "Arg int/float min/max/step wrong"
                    else:
                        assert isinstance(v, prop_pytype), "Arg int/float min/max/step wrong"
                used += ["min", "max", "step"]
            used.sort()
            prop_values = list(prop_values.keys())
            prop_values.sort()
            assert used == prop_values, "Arg unknown properties"

    def add_defaults(self, args, db_cursor=None, now=None):
        self._validate_args_definitions(args)
        self.props_defaults.add_dict_tree(args, db_cursor, now)

    def get_test_by_name(self, test_name, db_cursor=None, now=None):
        if now is None:
            now = db_ms_now()
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor
        cmd = self.sql.get_test_by_name(test_name, now)
        row = c.query_one(cmd)
        if row is None:
            return None
        return self._new_test_obj( row[0], row[1], row[2])

    def get_test_by_id(self, test_id, db_cursor=None):
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor
        row = c.query_one(self.sql.get_test_by_id(test_id))
        if row is None:
            return None
        return self._new_test_obj( row[0], row[1], row[2])

    def add_test(self, local_file, db_cursor=None, now=None, test_name=None):
        if now is None:
            now = db_ms_now()
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor

        tests_id_cache = self._new_tests_cache[1]
        if self._new_tests_cache[0] != c:
            tests_id_cache = {}
            self._new_tests_cache = (c, tests_id_cache)

        test_name = test_name if test_name else os.path.basename(local_file)

        existing = self.get_test_by_name(test_name, c, now)

        if existing:
            raise Exception("Test called \"%s\" already exists." % test_name)

        test_id, file_id = tests_id_cache.get((test_name, local_file), (None, None))
        if test_id is None:
            file_id = self._add_files(c, [local_file])[0] # Look up again if need be
            test_id = c.insert(self.sql.add_test(file_id, now))
            if db_cursor is None:
                db.commit()
            tests_id_cache[(test_name, local_file)] = (test_id, file_id)
        return self._new_test_obj( test_id, test_name, file_id)

    def _update_groups_with_new_test(self, groups, old_test, new_test, db_cursor=None, now=None):
        for group in groups:
            for n in range(0, len(group.tests)):
                test = group.tests[n]
                if test.id == old_test.id:
                    group.tests[n] = new_test
                    new_test.pending_properties = \
                        test.pending_properties
            group.updated_db(db_cursor, now)


    def _get_default_args(self, local_folder, db_cursor=None, now=None):
        r = {"exit_on_fail": { 'desc'   : 'Exit on Fail',
                               'type'   : 'bool',
                               'default': True }}
        defaults_file = os.path.join(local_folder, "args.yaml")
        org = self.props_defaults.get_as_dict_tree(db_cursor, now)
        r.update(org)
        if os.path.exists(defaults_file):
            with open(defaults_file) as f:
                defaults_gen=yaml.safe_load_all(f)
                new_args = [root for root in defaults_gen][0]
                for arg, arg_body in new_args.items():
                    old_arg = org.get(arg, None)
                    if old_arg:
                        # We can let something things change, but not type.
                        assert old_arg['type'] == arg_body['type'], "Type change of default arg."
                r.update(new_args)
        return r

    def group_from_dict(self, group_data, folder, db_cursor=None, now=None):
        if now is None:
            now = db_ms_now()
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor

        default_args = self._get_default_args(folder, c, now)
        tests = []
        for test_name, test_args in group_data["tests"]:
            if 'exit_on_fail' not in test_args:
                test_args['exit_on_fail'] = True

            if isinstance(test_name, list):
                assert len(test_name) == 2, "If list is given for testname, it should be name then filename. : " + str(testname)
                filename = test_name[1]
                test_name = test_name[0]
                test_obj = self.get_test_by_name(filename, c, now)
                test_filename = os.path.join(folder, filename)
                if test_obj:
                    test_obj = copy.copy(test_obj)
                    test_obj.name = test_name
            else:
                test_obj = self.get_test_by_name(test_name, c, now)
                test_filename = os.path.join(folder, test_name)
            if not test_obj:
                if not os.path.exists(test_filename):
                    raise Exception("Test file '%s' not found" % test_filename)
                file_args = get_args_in_src(test_filename)
                for arg in file_args:
                    if arg not in test_args:
                        arg_def = default_args.get(arg, None)
                        if arg_def is None:
                            raise Exception(f"Argument '{arg}' in test '{test_filename}' not defined yet.")
                        default_val = arg_def.get("default", None)
                        if default_val is not None:
                            test_args[arg] = default_val
                        else:
                            print('WARNING importing test "%s", argument "%s", no value at load time.' % (test_name, arg))
                test_obj = self.add_test(test_filename, c, now, test_name)
            for arg_key in test_args:
                arg_details = default_args[arg_key]
                if arg_details['type'] == 'file':
                    filename = test_args[arg_key]
                    if isinstance(filename, str):
                        file_id = self.get_file_id_by_name(filename, c)
                        if file_id is None:
                            filename = os.path.abspath(os.path.join(folder, filename))
                            file_id = self._add_files(c, [filename])[0]
                        test_args[arg_key] = (dbfile, filename, file_id)
            test_obj.pending_properties = test_args
            tests += [test_obj]

        test_used = {}
        for test in tests:
            if test.name in test_used:
                count = test_used[test.name]
                test.name += "#%02i" % count
                test_used[test.name] = count + 1
            else:
                test_used[test.name] = 1

        r = self.add_group(group_data["name"],
                           group_data["desc"],
                           tests, c, now, group_data.get("note", None))

        self.add_defaults(default_args, c, now)

        if db_cursor is None:
            db.commit()

        return r

    def load_groups(self, filename, db_cursor=None, now=None):
        db = self.db
        if db_cursor:
            c = db_cursor
        else:
            c = db.cursor()
        if not now:
            now = db_ms_now()
        r = []
        folder = os.path.dirname(filename)
        with open(filename) as f:
            root_def_gen=yaml.safe_load_all(f)
            root_def = [root for root in root_def_gen][0]

            templates = root_def.get('templates', {})
            groups_list = root_def['groups']

            for group_data in groups_list:
                template_name = group_data.get('template', None)
                if template_name:
                    template_tests = templates.get(template_name, None)
                    if not template_tests:
                        raise Exception("Template '%s' used but not found." % template_name)

                    group_tests = group_data.get('tests', [])

                    for n in range(0, len(template_tests)):
                        template_test = template_tests[n]
                        if n == len(group_tests):
                            group_tests += [ copy.copy(template_test) ]
                        else:
                            group_test = group_tests[n]
                            if not len(group_test):
                                group_tests[n] = copy.copy(template_test)

                    group_data['tests'] = group_tests

                r += [self.group_from_dict(group_data, folder, c, now)]

            if not db_cursor:
                db.commit()
        return r

    def get_db_now():
        return None

    def add_group(self, name, desc, tests, db_cursor=None, now=None, note=None):
        if now is None:
            now = db_ms_now()
        db = self.db
        if db_cursor is None:
            c = db.cursor()
        else:
            c = db_cursor
        group_id = c.insert(self.sql.add_test_group(name, desc, now, note))

        group = self._new_test_group( group_id, name, desc)

        order_pos = 0
        for test in tests:
            group.add_test(test, c, order_pos, now)
            order_pos += 1

        if db_cursor is None:
            db.commit()

        return group

    def get_sessions(self, session_ids):
        assert len(session_ids)
        cmd = self.sql.get_sessions(session_ids)
        r = []
        for row in self.db.query(cmd):
            group = self._new_test_group( row[2], row[3], row[4])
            r += [ test_group_sessions(group, self, row[0], row[1], row[5], row[6]) ]
        return r

    def load_filestores(self, db_def):

        file_stores = db_def['file_stores']

        smbs = file_stores.get("smb", None)
        if smbs:
            for filestore in smbs:
                self.add_filestore(filestore[0], filestore[1], True,
                                   smb_transferer.protocol_id)

        sftps = file_stores.get("sftp", None)
        if sftps:
            for filestore in sftps:
                self.add_filestore(filestore[0], filestore[1], True,
                                   sftp_transferer.protocol_id)

    def get_machine(self, machine_id):
        return db_tester_machine.get_by_id(self, machine_id)

    def get_own_machine(self):
        if not self.tester_machine:
            self.tester_machine = db_tester_machine.get_own_machine(self)
        return self.tester_machine

    def get_dev(self, dev_uuid):
        raise NotImplementedError

    def get_dev_by_sn(self, serial_number):
        raise NotImplementedError

    def get_tar_virtual_filestore(self):
        db = self.db
        sql = self.sql
        c = db.cursor()
        return c.query_one(self.sql.get_tar_virtual_filestore())

    def generate_csv(self, outfile, before=None, after=None):
        db = self.db
        sql = self.sql
        c = db.cursor()
        csv_results = c.query(sql.get_csv_results(before, after))
        try:
            with open(outfile, 'w', newline='', encoding='utf-8') as csv_file:
                csv_writer = csv.writer(csv_file, dialect='excel')
                csv_writer.writerow(("Test Group", "Serial Number", "Result", "Timestamp"))
                for i in csv_results:
                    row = list(i)
                    row[2] = "PASS" if row[2] else "FAIL"
                    csv_writer.writerow(row)
        except Exception as e:
            print(f"Error: couldn't open file {outfile}:\n{e}", flush=True)
            return False
        return True
