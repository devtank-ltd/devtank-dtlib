import os
import sys

from .db_common import py_type_from_db_type, db_ms_now, db_time


class tests_group_creator:
    def __init__(self, db, db_group=None):
        self.db = db
        self.tests = []
        self.props_defaults = {}
        self.name = ""
        self.description = ""
        self.db_group = None
        self.duration = None
        self.note = None
        self.passed = False
        if db:
            self.update_defaults()
        if db_group:
            self.populate_from(db_group)


    def update_defaults(self):
        self.props_defaults = self.db.props_defaults.get_as_dict_tree()
        for key, val in self.props_defaults.items():
            assert 'type' in val
            assert 'desc' in val

            val_type = py_type_from_db_type(val['type'])

            if val['type'] == 'int' or val['type'] == 'float':
                assert 'min' in val
                assert 'max' in val
                assert 'step' in val
            val['type'] = val_type


    def clear(self):
        self.name = ""
        self.tests = []
        self.description = ""
        self.db_group = None
        self.duration = None


    def populate_from(self, db_group, pynowtime=None):
        self.db_group = db_group
        self.name = db_group.name
        self.description = db_group.desc

        now = db_time(pynowtime) if pynowtime is not None else db_ms_now()

        self.tests = db_group.get_tests(now)

        self.note = db_group.note

        for test in self.tests:
            test.load_properties()

        self.duration = db_group.get_duration(now)
        self.passed = False


    def updated_db(self, db_cursor=None, now=None):
        if self.db_group:
            self.db_group.update(self.name, self.description,
                                 self.tests, db_cursor, now)
        else:
            self.db.add_group(self.name, self.description, self.tests,
                              db_cursor, now)


    def add_tests_results(self, devs, results):
        to_reduce = {}

        for uuid, uuid_results in results.items():
            uuid_test_results = uuid_results['tests']
            for test in self.tests:
                test_data = uuid_test_results.get(test.name, None)
                to_reduce["%s_%s" % (uuid, test.name)] = test_data.get('passfail', False) if test_data else False

        self.passed = min(to_reduce.values()) if len(to_reduce) else False

        return self.db_group.add_tests_results(devs, results, self.tests)

    def override_tests_properties(self, overrides):
        for test in self.tests:
            for prop in overrides:
                test.pending_properties[prop] = overrides[prop]

    def get_unset(self):
        r = []
        for test in self.tests:
            props = test.pending_properties
            p = []
            for key, value in props.items():
                if value is None:
                    p += [ key ]
            if p:
                r += [ (test, p) ]
        return r
