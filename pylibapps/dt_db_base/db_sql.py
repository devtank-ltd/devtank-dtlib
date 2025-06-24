import datetime
from dateutil.tz import tzlocal
import time
import sys


from .db_common import *
from .base import dt_get_build_info
from .db_filestore_protocol import tar_transferer


_id_null = lambda x: (f"{int(x)}") if x else "NULL"
_int_null = lambda x: (f"{int(x)}") if x is not None else "NULL"


class sql_common(object):
    def __init__(self):
        self.dev_result_table_name = "dev_result_table"
        self.dev_result_values_table_name = None
        self.devices_table_name = "devs"
        self.device_key_name = "dev_id"
        self.db_version = None

        self.defaults_id = 3
        self.settings_id = 2
        self.test_props_id = 4
        self.result_props_id = None

    def setup(self, db):
        cmd = self.get_dynamic_table_info()
        row = db.query_one(cmd)
        assert row, "Nothing returned for dynamic table names."
        self.use_dynamic_table_info(row)
        rows = db.query(self.get_result_values_parent_id())
        if rows:
            assert len(rows) == 1, "Should be one entry for results values parent."
            self.result_props_id = rows[0][0]

    ######################################################
    #                                                    #
    #                                                    #
    #               Device-Related SQL                   #
    #                                                    #
    #                                                    #
    ######################################################

    def get_update_dev_uid(self, dev_id, new_uuid):
        return f"""
        UPDATE {self.devices_table_name}
        SET uid='{db_safe_str(new_uuid)}'
        WHERE id={int(dev_id)}
        """

    def get_dev_by_serial(self, serial_number):
        return f"""
        SELECT serial_number, id, uid
        FROM {self.devices_table_name}
        WHERE serial_number='{db_safe_str(serial_number)}'
        """

    def get_dev_by_uid(self, uuid):
        return f"""
        SELECT serial_number, id, uid
        FROM {self.devices_table_name}
        WHERE uid='{db_safe_str(uuid)}'
        """

    def get_dev_by_id(self, dev_id):
        return f"""
        SELECT serial_number, id, uid
        FROM {self.devices_table_name}
        WHERE id={int(dev_id)}
        """

    def get_dev_status_since(self, timestamp):
        return f"""
        SELECT {self.devices_table_name}.uid,
        test_groups.name,
        MAX(test_group_results.time_of_tests),
        MIN(pass_fail)
        FROM test_group_results
        JOIN test_groups ON
        test_groups.id = test_group_results.group_id
        JOIN {self.dev_result_table_name}
        ON {self.dev_result_table_name}.group_result_id =
        test_group_results.id JOIN {self.devices_table_name} ON
        {self.devices_table_name}.id =
        {self.dev_result_table_name}.{self.device_key_name}
        WHERE time_of_tests > {timestamp}
        GROUP BY {self.devices_table_name}.id,
        test_groups.name
        """

    def get_dev_session_count(self, dev_id):
        return f"""
        SELECT COUNT(DISTINCT test_group_results.id)
        FROM {self.dev_result_table_name}
        JOIN test_group_results ON
        test_group_results.id = group_result_id
        WHERE {self.dev_result_table_name}.{self.device_key_name} =
        {int(dev_id)}
        """

    def get_dev_sessions(self, dev_id, offset, count):
        return f"""
        SELECT DISTINCT test_group_results.id,
        test_group_results.time_of_tests,
        test_group_results.group_id,
        mac, hostname FROM {self.dev_result_table_name}
        JOIN test_group_results ON
        test_group_results.id = group_result_id
        LEFT JOIN tester_machines ON
        tester_machines.id = tester_machine_id
        WHERE {self.dev_result_table_name}.{self.device_key_name} =
        {int(dev_id)} ORDER BY test_group_results.time_of_tests
        DESC LIMIT {int(count)} OFFSET {int(offset)}
        """

    def get_dev_last_session(self, dev_id, group_name):
        return f"""
        SELECT DISTINCT test_group_results.id,
        test_group_results.time_of_tests,
        test_group_results.group_id,
        mac, hostname FROM {self.dev_result_table_name}
        JOIN test_group_results ON
        test_group_results.id = group_result_id
        JOIN test_groups ON test_groups.id = test_group_results.group_id
        LEFT JOIN tester_machines ON
        tester_machines.id = tester_machine_id
        WHERE {self.dev_result_table_name}.{self.device_key_name} =
        {int(dev_id)} AND test_groups.name = '{db_safe_str(group_name)}'
        ORDER BY time_of_tests DESC LIMIT 1
        """

    def get_dev_last_result(self, dev_id, group_name, do_like=False):
        cmp_str = f"test_groups.name LIKE '%%{db_safe_str(group_name)}%%'" \
            if do_like else f"test_groups.name = '{db_safe_str(group_name)}'"
        return f"""
        SELECT test_group_results.time_of_tests,
        MIN({self.dev_result_table_name}.pass_fail)
        FROM {self.dev_result_table_name}
        JOIN test_group_results ON
        test_group_results.id = {self.dev_result_table_name}.group_result_id
        JOIN test_groups ON
        test_groups.id = test_group_results.group_id
        WHERE {self.dev_result_table_name}.{self.device_key_name} = {int(dev_id)}
        AND {cmp_str}
        GROUP BY test_group_results.time_of_tests
        ORDER BY test_group_results.time_of_tests
        DESC LIMIT 1
        """

    ####################################################
    #                                                  #
    #                                                  #
    #               File-Related SQL                   #
    #                                                  #
    #                                                  #
    ####################################################

    def add_file_store(self, server_name, server_folder, is_writable, protocol_id):
        return f"""
        INSERT INTO file_stores
        (server_name, base_folder, is_writable, protocol_id)
        VALUES('{db_safe_str(server_name)}',
        '{db_safe_str(server_folder)}',
        {int(is_writable)},
        {int(protocol_id)})
        """

    def update_file_store_writable(self, filestore_id, is_writable):
        return f"""
        UPDATE file_stores
        SET is_writable
        WHERE is_writable={int(is_writable)}
        AND id={int(filestore_id)}
        """

    def get_file_filestore(self, file_id):
        return f"""
        SELECT server_name, protocol_id, base_folder,
        filename, modified_date, size
        FROM files JOIN file_stores ON
        file_store_id=file_stores.id WHERE files.id={int(file_id)}
        """

    def get_all_file_stores(self):
        return """
        SELECT id, server_name,
        base_folder, protocol_id, is_writable
        FROM file_stores
        """

    def get_rw_file_store(self):
        return """
        SELECT id, server_name, base_folder, protocol_id
        FROM file_stores WHERE is_writable=1 LIMIT 1
        """

    def get_resource_files(self):
        dev_result_table_name = self.dev_result_table_name
        return '''
        SELECT files.id, files.filename
        FROM files JOIN "values"
        ON value_file_id = files.id
        '''

    def get_file_by_name(self, name):
        return f"""
        SELECT files.id FROM files WHERE files.filename='{name}'
        """

    def add_file(self, filename, filestore_id, now, mod_time, size):
        return f"""
        INSERT INTO files
        (filename, file_store_id, insert_time, modified_date, size)
        VALUES('{db_safe_str(filename)}', {int(filestore_id)},
        {int(now)}, {int(mod_time)}, {int(size)})
        """

    def get_filename(self, file_id):
        return f"""
        SELECT filename FROM files WHERE files.id={int(file_id)}
        """

    def get_tar_virtual_filestore(self):
        return f"""
        SELECT id, protocol_id FROM file_stores
        WHERE server_name = '{db_safe_str(tar_transferer.server_name)}'
        """

    def add_file_store_protocol(self, protocol_name):
        return f"""
        INSERT INTO file_store_protocols (name) VALUES ('{db_safe_str(protocol_name)}');
        """

    def get_file_store_protocol_id(self, protocol_name):
        return f"""
        SELECT id FROM file_store_protocols
        WHERE name='{db_safe_str(protocol_name)}'
        """

    def link_tar_file(self, tar_file_id, file_id):
        return f"""
        INSERT INTO tar_files
        (parent_file_id, file_id) VALUES
        ({tar_file_id}, {file_id})
        """

    def complete_tar_file(self, tar_file_id, modtime, filesize):
        return f"""
        UPDATE files SET modified_date={modtime}, size={filesize}
        WHERE id={tar_file_id}
        """

    def get_tar_id(self, file_id):
        return f"""
        SELECT parent_file_id FROM tar_files WHERE file_id={file_id}
        """

    ####################################################
    #                                                  #
    #                                                  #
    #               Test-Related SQL                   #
    #                                                  #
    #                                                  #
    ####################################################

    def add_test(self, file_id, valid_from):
        return f"""
        INSERT INTO tests (file_id, valid_from)
        VALUES({int(file_id)}, {int(valid_from)})
        """

    def get_all_tests(self, now):
        return f"""
        SELECT tests.id, files.filename, tests.file_id
        FROM tests JOIN files ON tests.file_id=files.id
        WHERE tests.valid_from<={int(now)}
        AND (tests.valid_to IS NULL OR tests.valid_to>{int(now)})
        """

    def get_test_by_id(self, test_id):
        return f"""
        SELECT tests.id, files.filename, tests.file_id
        FROM tests JOIN files ON tests.file_id=files.id
        WHERE tests.id={int(test_id)}
        """

    def get_test_by_name(self, name, now):
        return f"""
        SELECT tests.id, files.filename, tests.file_id
        FROM tests JOIN files ON tests.file_id=files.id
        WHERE files.filename='{db_safe_str(name)}'
        AND tests.valid_from<={int(now)}
        AND (tests.valid_to IS NULL
        OR tests.valid_to>{int(now)})
        """

    def get_deleted_tests(self, now):
        return f"""
        SELECT tests.id, files.filename, tests.file_id
        FROM tests JOIN files ON tests.file_id=files.id
        WHERE tests.id
        IN (SELECT MAX(tests.id)
        FROM tests WHERE tests.valid_from<={int(now)}
        AND tests.valid_to<={int(now)} AND tests.file_id
        NOT IN (SELECT tests.file_id FROM tests JOIN files
        ON tests.file_id=files.id WHERE tests.valid_from<={int(now)}
        AND (tests.valid_to IS NULL
        OR tests.valid_to>{int(now)})) GROUP BY file_id )
        """

    def remove_test(self, test_id, now):
        return f"""
        UPDATE tests SET valid_to={int(now)} WHERE id={int(test_id)}
        """

    def get_tests(self, group_id, now):
        dev_result_table_name = self.dev_result_table_name
        return f"""
        SELECT tests.id, filename, file_id, test_group_entries.name,
        test_group_entries.id AS entry_id, order_position,
        (SELECT MAX(duration) FROM {dev_result_table_name}
        WHERE group_entry_id=test_group_entries.id
        AND pass_fail=1) AS duration FROM tests JOIN files
        ON tests.file_id=files.id JOIN test_group_entries
        ON test_group_entries.Test_id=tests.id
        WHERE (test_group_entries.valid_to IS NULL
        OR test_group_entries.valid_to>{int(now)})
        AND test_group_entries.valid_from<={int(now)}
        AND test_group_entries.test_group_id={int(group_id)}
        ORDER BY order_position
        """

    ###########################################################
    #                                                         #
    #                                                         #
    #               Tests Group-Related SQL                   #
    #                                                         #
    #                                                         #
    ###########################################################

    def add_test_group(self, name, desc, valid_from, note=None):
        return f"""
        INSERT INTO test_groups
        (name, description, creation_note, valid_from)
        VALUES('{db_safe_str(name)}',
        '{db_safe_str(desc)}',
        {db_safe_null_str(note)},
        {int(valid_from)})
        """

    def add_test_group_test(self, group_id, test_id, name, order_pos, valid_from):
        return f"""
        INSERT INTO test_group_entries
        (test_group_id, Test_id, name, order_position, valid_from)
        VALUES
        ({int(group_id)},
        {int(test_id)},
        '{db_safe_str(name)}',
        {int(order_pos)},
        {int(valid_from)})
        """

    def add_test_group_value(self, group_entry_id, value_id):
        return f"""
        INSERT INTO test_group_entry_properties
        (group_entry_id, Value_id)
        VALUES({int(group_entry_id)}, {int(value_id)})
        """

    def remove_test_group_tests(self, group_entry_id, now):
        return f"""
        UPDATE test_group_entries SET
        valid_to={int(now)} WHERE id={int(group_entry_id)}
        """

    _GROUP_SQL = "SELECT id, name, description FROM test_groups "

    def get_groups(self, now):
        return f"""
        {self._GROUP_SQL}WHERE valid_from<={int(now)}
        AND (valid_to IS NULL OR valid_to>{int(now)})
        """

    def get_group_by_name(self, name, now):
        return f"""
        {self._GROUP_SQL}WHERE valid_from<={int(now)}
        AND (valid_to IS NULL OR valid_to>{int(now)})
        AND name='{db_safe_str(name)}'
        """

    def get_group_by_id(self, group_id):
        return f"""
        {self._GROUP_SQL} WHERE id={int(group_id)}
        """

    def get_group_name_versions(self, name):
        return f"""
        SELECT * FROM
        ( SELECT test_groups.id,
        test_groups.valid_from
        FROM test_groups JOIN test_group_entries
        ON test_group_entries.test_group_id = test_groups.id
        WHERE test_groups.name='{db_safe_str(name)}'
        UNION SELECT test_groups.id, test_group_entries.valid_from
        FROM test_groups JOIN test_group_entries
        ON test_group_entries.test_group_id = test_groups.id
        WHERE test_groups.name='{db_safe_str(name)}' ) AS temp
        GROUP BY id, valid_from
        ORDER BY valid_from
        """

    def remove_test_group(self, group_id, now):
        return f"""
        UPDATE test_groups SET valid_to={int(now)}
        WHERE id={int(group_id)}
        """

    def set_test_group_name(self, group_id, name):
        return f"""UPDATE test_groups
        SET name='{db_safe_str(name)}'
        WHERE id={int(group_id)}
        """

    def set_test_group_desc(self, group_id, desc):
        return f"""
        UPDATE test_groups SET description='{db_safe_str(desc)}'
        WHERE id={int(group_id)}
        """

    def get_test_group_durations(self, group_id, now):
        dev_result_table_name = self.dev_result_table_name
        return f"""
        SELECT test_group_entries.id,
        MAX({dev_result_table_name}.duration) FROM test_group_entries
        JOIN {dev_result_table_name}
        ON {dev_result_table_name}.group_entry_id=test_group_entries.id
        WHERE test_group_id={int(group_id)} AND valid_from<={int(now)}
        AND (valid_to IS NULL OR valid_to>{int(now)})
        GROUP BY test_group_entries.id
        """

    def get_test_group_creation_note(self, group_id):
        return f"""
        SELECT creation_note FROM test_groups WHERE id={int(group_id)}
        """

    #######################################################
    #                                                     #
    #                                                     #
    #               Results-Related SQL                   #
    #                                                     #
    #                                                     #
    #######################################################

    def add_test_group_results(self, group_id, machine_id, now, is_pass=None):
        if self.db_version > 3:
            tz = tzlocal()
            tz_name = tz.tzname(datetime.datetime.now(tz))
            sw_git_sha1 = dt_get_build_info()[1][:7]
            if self.db_version > 5:
                assert isinstance(is_pass, bool), "Invalid is_pass given."
                return f"""
                    INSERT INTO test_group_results
                    (group_id, time_Of_tests, logs_tz_name,
                     tester_machine_id, sw_git_sha1, is_pass)
                    VALUES ({int(group_id)},
                    {int(now)}, '{db_safe_str(tz_name)}',
                    {_id_null(machine_id)}, '{db_safe_str(sw_git_sha1)}',
                    {int(is_pass)})
                    """
            else:
                return f"""
                    INSERT INTO test_group_results
                    (group_id, time_Of_tests, logs_tz_name, tester_machine_id, sw_git_sha1)
                    VALUES ({int(group_id)},
                    {int(now)}, '{db_safe_str(tz_name)}',
                    {_id_null(machine_id)}, '{db_safe_str(sw_git_sha1)}')
                    """
        else:
            return f"""
            INSERT INTO test_group_results (group_id, Time_Of_tests)
            VALUES ({int(group_id)}, {int(now)})
            """

    def get_test_group_results_count(self, group_id):
        return f"""
        SELECT COUNT(id) FROM test_group_results WHERE group_id={int(group_id)}
        """

    def get_test_group_results_count_by_name(self, group_name):
        return f"""
        SELECT COUNT(test_group_results.id) FROM test_group_results
        JOIN test_groups ON test_groups.id = test_group_results.group_id
        WHERE test_groups.name='{db_safe_str(group_name)}'
        """

    def get_test_group_results(self, group_id, offset, count):
        return f"""
        SELECT test_group_results.id, Time_Of_tests, mac, hostname
        FROM test_group_results LEFT JOIN tester_machines
        ON tester_machines.id = test_group_results.tester_machine_id
        WHERE group_id={int(group_id)}
        ORDER BY Time_Of_tests DESC LIMIT {int(count)}
        OFFSET {int(offset)}
        """

    def get_test_group_results_by_name(self, group_name, offset, count):
        return f"""
        SELECT test_group_results.id, Time_Of_tests, group_id, mac, hostname
        FROM test_group_results JOIN test_groups
        ON test_groups.id = test_group_results.group_id
        LEFT JOIN tester_machines
        ON tester_machines.id = test_group_results.tester_machine_id
        WHERE test_groups.name='{db_safe_str(group_name)}'
        ORDER BY Time_Of_tests
        DESC LIMIT {int(count)} OFFSET {int(offset)}
        """

    def get_sessions(self, session_ids):
        return f"""
        SELECT test_group_results.id, Time_Of_tests,
        group_id, name, description, mac, hostname
        FROM test_group_results JOIN test_groups
        ON test_groups.id = group_id LEFT JOIN tester_machines
        ON tester_machines.id = test_group_results.tester_machine_id
        WHERE test_group_results.id
        IN ({','.join([str(session_id) for session_id in session_ids])})
        ORDER BY Time_Of_tests DESC
        """

    def get_dev_results(self, session_id):
        return f"""
        SELECT {self.dev_result_table_name}.id, {self.devices_table_name}.uid,
        pass_fail, output_file_id, log_file_id, Test_id, name, filename,
        order_position, duration, {self.devices_table_name}.serial_number
        FROM {self.dev_result_table_name} JOIN test_group_entries
        ON test_group_entries.id = {self.dev_result_table_name}.group_entry_id
        JOIN tests ON tests.id = test_group_entries.Test_id
        JOIN files ON files.id = tests.file_id
        JOIN {self.devices_table_name}
        ON {self.devices_table_name}.id = {self.dev_result_table_name}.{self.device_key_name}
        WHERE {self.dev_result_table_name}.group_result_id={session_id}
        """

    def get_test_group_results_tests(self, session_id, now):
        return f"""
        SELECT test_group_entries.order_position, test_group_entries.name,
        files.filename, test_group_entries.id AS group_entry_id
        FROM test_group_results JOIN test_groups ON test_groups.id = group_id
        JOIN test_group_entries ON test_group_entries.test_group_id = group_id
        JOIN tests ON tests.id = test_group_entries.Test_id JOIN files ON
        files.id = tests.file_id WHERE test_group_results.id={int(session_id)}
        AND test_group_entries.valid_from<={int(now)}
        AND (test_group_entries.valid_to IS NULL
        OR test_group_entries.valid_to>{int(now)})
        ORDER BY test_group_entries.order_position
        """

    def add_dev_result(
        self,
        session_id,
        dev_id,
        group_entry_id,
        pass_fail,
        output_file_id,
        log_file_id,
        duration,
    ):
        dev_result_table_name = self.dev_result_table_name
        device_key_name = self.device_key_name
        return f"""
        INSERT INTO {dev_result_table_name}
        (group_result_id, {device_key_name}, group_entry_id, pass_fail,
        output_file_id,log_file_id, duration)
        VALUES ({int(session_id)}, {int(dev_id)}, {int(group_entry_id)},
        {int(int(pass_fail))}, {_id_null(output_file_id)},
        {_id_null(log_file_id)}, {_int_null(duration)})
        """

    def add_test_value(self, result_id, value_id):
        dev_result_values_table_name = self.dev_result_values_table_name
        if not dev_result_values_table_name:
            return None
        return f"""
        INSERT INTO {dev_result_values_table_name}
        (test_result_id, value_id) VALUES ({int(result_id)}, {int(value_id)})
        """

    def get_csv_results(self, before=None, after=None):
        r = f"""
        SELECT name,
               serial_number,
               pass_fail,
               DATETIME(ROUND(time_of_tests / 1000000), 'unixepoch', 'localtime') AS time_of_tests
        FROM {self.devices_table_name} as dd
        JOIN {self.dev_result_table_name} AS tr ON dd.id = tr.{self.device_key_name}
        JOIN test_group_results AS gr ON tr.group_result_id = gr.id
        JOIN test_groups AS tg ON gr.group_id = tg.id
        """
        if before:
            r += f"WHERE time_of_tests < {before}"
            if after:
                r += f" AND time_of_tests > {after}"
        elif after:
            r += f" WHERE time_of_tests > {after}"

        return r + ";"

    ######################################################
    #                                                    #
    #                                                    #
    #               Values-Related SQL                   #
    #                                                    #
    #                                                    #
    ######################################################

    def get_version(self):
        return '''
        SELECT name, value_int FROM "values" WHERE id=1
        '''

    def add_default_value(self, name, valid_from):
        return f"""
        INSERT INTO \"values\" (name, parent_id, valid_from)
        VALUES('{db_safe_str(name)}',
        {int(self.defaults_id)}, {int(valid_from)})
        """

    def add_default_value_str_param(self, name, parent_id, value, valid_from):
        return f"""
        INSERT INTO \"values\" (name, parent_id, value_text, valid_from)
        VALUES('{db_safe_str(name)}', {int(parent_id)},
        '{db_safe_str(value)}', {int(valid_from)})
        """

    def add_default_value_int_param(self, name, parent_id, value, valid_from):
        return f"""
        INSERT INTO \"values\" (name, parent_id, value_int, valid_from)
        VALUES('{db_safe_str(name)}', {int(parent_id)},
        {int(value)}, {int(valid_from)})
        """

    def add_default_value_flt_param(self, name, parent_id, value, valid_from):
        return f"""
        INSERT INTO \"values\" (name, parent_id, value_real, valid_from)
        VALUES('{db_safe_str(name)}', {int(parent_id)},
        {value:g}, {int(valid_from)})
        """

    def add_default_value_file_param(self, name, parent_id, value, valid_from):
        return f"""
        INSERT INTO \"values\" (name, parent_id, 'value_file_id', valid_from)
        VALUES('{db_safe_str(name)}', {int(parent_id)}, {value:g},
        {int(valid_from)})
        """

    def get_value(self, parent_id, now):
        return f'''
        SELECT id, name, value_text, value_int, value_real, value_file_id
        FROM "values" WHERE parent_id={int(parent_id)}
        AND valid_from<={int(now)} AND (valid_to IS NULL
        OR valid_to>{int(now)})
        '''

    def get_value_by_name(self, parent_id, name, now):
        return f"""
        SELECT id, name, value_text, value_int, value_real, value_file_id
        FROM \"values\" WHERE parent_id={int(parent_id)}
        AND valid_from<={int(now)} AND (valid_to IS NULL
        OR valid_to>{int(now)}) AND name='{db_safe_str(name)}'
        """

    def add_null_value(self, name, valid_from, parent_id):
        return f"""
        INSERT INTO \"values\" (name, parent_id, valid_from)
        VALUES('{db_safe_str(name)}', {int(parent_id)}, {int(valid_from)})
        """

    def add_value(self, name, value_column, value, valid_from, parent_id):
        return f"""
        INSERT INTO \"values\" (name, {db_safe_name(value_column)},
        parent_id, valid_from)
        VALUES('{db_safe_str(name)}',{value},
        {int(parent_id)}, {int(valid_from)})
        """

    def get_result_values_parent_id(self):
        return """
        SELECT id FROM \"values\" WHERE parent_id
        IS NULL AND name='results_values'
        """

    def disable_value_by_name(self, parent_id, name, now):
        return f"""
        UPDATE \"values\" SET valid_to={int(now)} WHERE
        name='{name}' AND parent_id={int(parent_id)}
        """

    def disable_value(self, value_id, now):
        return f'''
        UPDATE "values" SET valid_to={int(now)}
        WHERE id={int(value_id)}
        '''

    def get_test_properties(self, group_entry_id):
        return f'''
        SELECT name, Value_text, value_int, value_real, value_file_id
        FROM test_group_entry_properties JOIN "values"
        ON "values".id = Value_id
        WHERE test_group_entry_properties.group_entry_id={int(group_entry_id)}
        '''

    def get_dynamic_table_info(self):
        return '''
        SELECT (SELECT value_text FROM "values"
        WHERE name=\'dev_table\' AND parent_id=2) as dev_table,
        (SELECT value_text FROM "values" WHERE name=\'dev_results_table\'
        AND parent_id=2) as dev_results_table,
        (SELECT value_text FROM "values" WHERE name=\'dev_results_table_key\'
        AND parent_id=2) as dev_results_table_key,
        (SELECT value_text FROM "values"
        WHERE name=\'dev_results_values_table\'
        AND parent_id=2) as dev_results_values_table,
        (SELECT value_int FROM "values" WHERE id=1) as db_version
        '''

    def use_dynamic_table_info(self, row):
        self.devices_table_name = row[0]
        self.dev_result_table_name = row[1]
        self.device_key_name = row[2]
        self.dev_result_values_table_name = row[3]
        self.db_version = row[4]

    ##############################################################
    #                                                            #
    #                                                            #
    #               Tester-Machine-Related SQL                   #
    #                                                            #
    #                                                            #
    ##############################################################

    _MACHINE_SQL = "SELECT id, mac, hostname FROM tester_machines"

    def get_machine_by_id(self, machine_id):
        return f"""
        {self._MACHINE_SQL} WHERE id={int(machine_id)}
        """

    def get_machine(self, mac, hostname):
        return f"""
        {self._MACHINE_SQL} WHERE mac='{db_safe_str(mac).lower()}'
        AND lower(hostname)='{db_safe_str(hostname).lower()}'
        """

    def add_machine(self, mac, hostname):
        return f"""
        INSERT INTO tester_machines (mac, hostname)
        VALUES('{db_safe_str(mac).lower()}','{db_safe_str(hostname)}')
        """

    def get_all_machines(self):
        return self._MACHINE_SQL

    def get_machine_sessions_count(self, machine_id):
        return f"""
        SELECT COUNT(id) FROM test_group_results
        WHERE tester_machine_id={int(machine_id)}
        """

    def get_machine_sessions(self, machine_id, offset, count):
        return f"""
        SELECT test_group_results.group_id, test_group_results.id, Time_Of_tests, mac, hostname
        FROM test_group_results LEFT JOIN tester_machines
        ON tester_machines.id = test_group_results.tester_machine_id
        WHERE tester_machine_id={int(machine_id)}
        ORDER BY Time_Of_tests DESC LIMIT {int(count)}
        OFFSET {int(offset)}
        """
