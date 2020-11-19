from __future__ import print_function
import mysql.connector as mysqlconn
import paramiko
import sqlite3
import yaml
import shutil
import hashlib
import datetime
import copy
import sys
import os


class db_obj_t(object):
    def __init__(self, id, name, valid_from, valid_to):
        self.id = id
        self.name = name
        self.valid_from = valid_from
        self.valid_to = valid_to

    def is_valid_at(self, timestamp):
        if timestamp is None:
            return self.valid_to is None
        return self.valid_from <= timestamp and \
            (not self.valid_to or timestamp < self.valid_to)

    def _asdict(self):
        d = dir(self)
        r = {}
        for k in d:
            if not k.startswith("_") and k != "is_valid_at":
                r[k] = getattr(self, k)
        return r


class test_group_t(db_obj_t):
    def __init__(self, id, name, desc, notes, valid_from, valid_to, entries):
        db_obj_t.__init__(self, id, name, valid_from, valid_to)
        self.desc = desc
        self.entries = entries
        self.notes = notes


class group_entry_t(db_obj_t):
    def __init__(self, id, name, pos, valid_from, valid_to, args, test):
        db_obj_t.__init__(self, id, name, valid_from, valid_to)
        self.pos = pos
        self.args = args
        self.test = test

class test_t(db_obj_t):
    def __init__(self, id, name, file_key, file_id, valid_from, valid_to):
        db_obj_t.__init__(self, id, name, valid_from, valid_to)
        self.file_key = file_key
        self.file_id = file_id


class arg_t(db_obj_t):
    def __init__(self, id, name, text, int, real, file_key, file_id, valid_from, valid_to):
        db_obj_t.__init__(self, id, name, valid_from, valid_to)
        self.text = text
        self.int = int
        self.real = real
        self.file_key = file_key
        self.file_id = file_id


def obj_valid_at(obj, timestamp):
    return obj.is_valid_at(timestamp)

def as_human_time(unix_usec):
    if not unix_usec or unix_usec == float("inf") or unix_usec == float("-inf"):
        return "None"
    return datetime.datetime.utcfromtimestamp(unix_usec / 1000000).strftime('%Y-%m-%d %H:%M:%S')



class db_process_t(object):
    def __init__(self):
        self.dev_table = "example_devs"
        self.results_table = "example_dev_test_results"
        self.results_table_dev = "example_dev_id"
        self.results_values_table = "example_dev_results_values_table"
        self.db_paths = {}
        self.dbrefs = {}
        self.ssh_connections = {}

    def load_custom_table_names(self, c):
        cmd = 'SELECT value_text FROM "values" WHERE name=\'dev_table\' AND parent_id=2'
        c.execute(cmd)
        row = c.fetchone()
        self.dev_table = row[0]
        cmd = 'SELECT value_text FROM "values" WHERE name=\'dev_results_table\' AND parent_id=2'
        c.execute(cmd)
        row = c.fetchone()
        self.results_table = row[0]
        cmd = 'SELECT value_text FROM "values" WHERE name=\'dev_results_table_key\' AND parent_id=2'
        c.execute(cmd)
        row = c.fetchone()
        self.results_table_dev = row[0]
        cmd = 'SELECT value_text FROM "values" WHERE name=\'dev_results_values_table\' AND parent_id=2'
        c.execute(cmd)
        row = c.fetchone()
        self.results_values_table = row[0]

    def debug_print(self, level, msg):
        log_level = int(os.environ.get("DEBUG", 0))
        if level <= log_level:
            print(msg)

    def db_open(self, db_url):
        if db_url[0]=='{':
            db_def_gen=yaml.safe_load_all(db_url)
            db_def = [root for root in db_def_gen][0]
            print ('Opening "%s" on "%s"' % (db_def["dbname"], db_def["host"]), file=sys.stderr)
            db = mysqlconn.connect(database=db_def["dbname"],
                                   user=db_def["user"],
                                   password=db_def["password"],
                                   host=db_def["host"],
                                   port=db_def.get("port", 3306))
            db.sql_mode = 'ANSI'
            c = db.cursor(buffered=True)
            self.dbrefs[c] = db
            self.db_paths[c] = db_def
            return c
        else:
            sqlite_path=db_url
            print ("Opening :", sqlite_path, file=sys.stderr)
            db = sqlite3.connect(sqlite_path)
            if sys.version_info[0] > 2:
                db.set_trace_callback(lambda msg: self.debug_print(2, msg))
            folder_path = os.path.dirname(sqlite_path)
            c = db.cursor()
            self.dbrefs[c] = db
            self.db_paths[c] = folder_path
            return c


    def get_hash_folders(self, filename):
        hash_md5 = hashlib.md5()
        hash_md5.update(filename.encode())
        h = hash_md5.hexdigest()
        return [ h[n:n+2] for n in range(0, 8, 2) ]

    def get_batch_folders(self, file_id):
        r = ["files"]
        while file_id > 1000:
            file_id = int(file_id / 1000)
            r += ["_%0003u_" % (file_id % 1000)]
        r.reverse()
        return r

    def get_rw_file_store(self, c):
        cmd = "SELECT MAX(id) FROM file_stores WHERE is_writable=1"
        c.execute(cmd)
        row = c.fetchone()
        assert row, "No writable filestore."
        return row[0]

    def get_db_folder(self, c):
        cmd = "SELECT name, server_name, base_folder FROM file_stores \
    JOIN file_store_protocols ON \
    file_store_protocols.id = file_stores.protocol_id \
    WHERE is_writable=1 ORDER BY file_stores.id DESC"
        c.execute(cmd)
        rows = c.fetchall()
        assert len(rows) == 1
        row = rows[0]

        assert row[0] == "SFTP"
        hostname=row[1]
        folder = row[2]
        
        db_path = self.db_paths[c]

        if isinstance(db_path, str):
            assert hostname == "LOCALHOST"

            if not os.path.exists(folder):
                folder = os.path.join(db_path, "db_files")

            return folder
        else:
            if hostname in self.ssh_connections:
                sftp, ssh = self.ssh_connections[hostname]
            else:
                db_def = db_path
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(hostname, username=db_def.get("sftp_user", None), password=db_def.get("sftp_password", None))
                sftp=ssh.open_sftp()
                self.ssh_connections[hostname]=(sftp, ssh)
            return (sftp, row[2])

    def remote_exists(self, sftp, path):
        try:
            sftp.stat(path)
            return True
        except:
            return False

    def get_file(self, c, file_id):

        cmd = "SELECT file_store_protocols.name,\
                      file_stores.server_name, \
                      file_stores.base_folder, \
                      files.filename \
    FROM files \
    JOIN file_stores ON file_stores.id = files.file_store_id \
    JOIN file_store_protocols ON file_store_protocols.id = file_stores.protocol_id \
    WHERE files.id = %u " % file_id
        c.execute(cmd)

        row = c.fetchone()

        assert row[0] == "SFTP"
        hostname = row[1]
        folder = row[2]

        filename = row[3]

        remote_filename = "%i.%s" % (file_id, filename)

        db_path = self.db_paths[c]

        if isinstance(db_path, str):
            assert row[1] == "LOCALHOST"
            folder = os.path.join(db_path, "db_files")
            folders = self.get_batch_folders(file_id)
            remote_path = os.path.join(folder, *folders)
            remote_path = os.path.join(remote_path, remote_filename)
            if not os.path.exists(remote_path):
                folders = self.get_hash_folders(remote_filename)
                remote_path = os.path.join(folder, *folders)
                remote_path = os.path.join(remote_path, remote_filename)
                if not os.path.exists(remote_path):
                    remote_path = os.path.join(folder, remote_filename)

            assert os.path.exists(remote_path), remote_path
            return remote_path

        else:
            if hostname in self.ssh_connections:
                sftp, ssh = self.ssh_connections[hostname]
            else:
                db_def = db_path
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(hostname, username=db_def.get("sftp_user", None), password=db_def.get("sftp_password", None))
                sftp=ssh.open_sftp()
                self.ssh_connections[hostname]=(sftp, ssh)

            folders = self.get_batch_folders(file_id)
            remote_path = os.path.join(folder, *folders)
            remote_path = os.path.join(remote_path, remote_filename)
            if not self.remote_exists(sftp, remote_path):
                folders = self.get_hash_folders(remote_filename)
                remote_path = os.path.join(folder, *folders)
                remote_path = os.path.join(remote_path, remote_filename)
                if not self.remote_exists(sftp, remote_path):
                    remote_path = os.path.join(folder, remote_filename)

            sftp.get(remote_path, local_path)
            return local_path


    def copy_file(self, folder, filepath, filename, file_id):
       remote_filename = "%i.%s" % (file_id, filename)
       folders = self.get_batch_folders(file_id)

       if isinstance(folder, str):
            path = folder
            for subfolder in folders:
                path = os.path.join(path, subfolder)
                if not os.path.exists(path):
                    os.makedirs(path)
            new_path = os.path.join(folder, *folders)
            new_path = os.path.join(new_path, remote_filename)
            shutil.copyfile(filepath, new_path)
       else:
            sftp, folder = folder
            path = folder
            for subfolder in folders:
                path = os.path.join(path, subfolder)
                try:
                    sftp.stat(path)
                except:
                    sftp.mkdir(path)

            new_path = os.path.join(folder, *folders)
            new_path = os.path.join(new_path, remote_filename)
            sftp.put(filepath, new_path)

    def add_file(self, c, filepath, now):
        cmd = "SELECT id FROM file_stores WHERE is_writable=1 ORDER BY id DESC"
        c.execute(cmd)
        fs_id  = c.fetchone()[0]
        cmd = "INSERT INTO files (file_store_id, filename, size, modified_date, insert_time) VALUES(%u, '%s', %u, %u, %u)" % (fs_id, os.path.basename(filepath), os.path.getsize(filepath), os.path.getmtime(filepath), now)
        c.execute(cmd) 
        file_id = c.lastrowid
        self.copy_file(self.get_db_folder(c), filepath, os.path.basename(filepath), file_id)
        return file_id

    def get_line(self, filepath, key):
        with open(filepath, "rb") as f:
            for line in f:
                if line.find(key) != -1:
                    return line

    def find_text(self, filepath, text_list):
        with open(filepath, "rb") as f:
            for line in f:
                for n in range(0, len(text_list)):
                    if line.find(text_list[n]) != -1:
                        return n, line
        return -1

    def db_time_to_str(self, db_time):
        seconds = db_time / 1000000.0
        minutes = int(seconds/60)
        seconds %= 60
        hours = int(minutes/60)
        minutes %= 60
        if hours:
            return "%02u:%02u:%02u" % (hours, minutes, seconds)
        return "%02u:%02u" % (minutes, seconds)

    def get_file_key(self, c, file_id, filename, filesize, is_result=False):
        if not is_result:
            filepath = self.get_file(c, file_id)
            filesize = os.path.getsize(filepath)
            md5 = hashlib.md5(open(filepath,'rb').read()).hexdigest()
        else:
            # If it's a result file, there is no point getting it and hashing it as the name is unique
            md5 = 0
        return (filename, filesize, md5)

    def get_file_key_from_id(self, c, file_id):
        c.execute("SELECT id, filename, size FROM files WHERE id=%u" % file_id)
        return self.get_file_key(c, *c.fetchone())

    def get_tests(self, c, tests_ids=None):
        cmd = "SELECT tests.id, files.filename, tests.file_id, tests.valid_from, tests.valid_to \
               FROM tests JOIN files ON files.id = tests.file_id"
        if tests_ids:
            cmd += " WHERE tests.id IN (" + ",".join([str(test_id) for test_id in tests_ids]) + ") "
        c.execute(cmd)
        rows = c.fetchall()

        tests_id_map = {}
        tests_name_map = {}

        for row in rows:
            tests_id, test_filename, tests_file_id, tests_valid_from, tests_valid_to = row
            file_key = self.get_file_key_from_id(c, tests_file_id)
            test = test_t(tests_id, test_filename, file_key, tests_file_id, tests_valid_from, tests_valid_to)
            tests_name_map.setdefault(test_filename, [])
            tests_name_map[test_filename] += [ test ]
            tests_id_map[tests_id] = test

        return tests_id_map, tests_name_map

    def get_groups(self, c, group_ids=None):
        cmd = 'SELECT test_groups.id, test_groups.name, test_groups.description, test_groups.notes, test_groups.valid_from, test_groups.valid_to,\
           test_group_entries.id, test_group_entries.name, test_group_entries.order_position, test_group_entries.valid_from, test_group_entries.valid_to,\
           tests.id, files.filename, tests.file_id, tests.valid_from, tests.valid_to,\
          "values".id, "values".name, "values".value_text, "values".value_int, "values".value_real, "values".value_file_id, "values".valid_from, "values".valid_to \
    FROM test_groups \
    LEFT JOIN test_group_entries ON test_group_entries.test_group_id=test_groups.id \
    LEFT JOIN test_group_entry_properties ON test_group_entry_properties.group_entry_id = test_group_entries.id \
    LEFT JOIN "values" ON "values".id = value_id \
    LEFT JOIN tests ON tests.id = test_group_entries.test_id \
    LEFT JOIN files ON files.id = tests.file_id '

        if group_ids:
            cmd += "WHERE test_groups.id IN (" + ",".join([str(group_id) for group_id in group_ids]) + ") "

        cmd += "ORDER BY test_groups.id, test_group_entries.order_position DESC, test_group_entry_properties.id DESC"
        c.execute(cmd)
        rows = c.fetchall()

        groups_id_map = {}
        groups_name_map = {}

        last_group_id = None
        last_entry_id = None
        current_entry = None
        current_group = None

        args = []
        entries = []

        if len(rows):
            rows += [[ None ] * len(rows[0])]

        for row in rows:
            test_groups_id, test_groups_name, test_groups_description, test_groups_notes, test_groups_valid_from, test_groups_valid_to,\
            test_group_entries_id, test_group_entries_name, test_group_entries_order_position, test_group_entries_valid_from, test_group_entries_valid_to,\
            tests_id, test_filename, tests_file_id, tests_valid_from, tests_valid_to, \
            values_id, values_name, values_value_text, values_value_int, values_value_real, values_value_file_id, values_valid_from, values_valid_to = row

            file_key = None
            if values_value_file_id:
                file_key = self.get_file_key_from_id(c, values_value_file_id)

            if values_id:
                arg = arg_t(values_id, values_name, values_value_text, values_value_int, values_value_real, file_key, values_value_file_id, values_valid_from, values_valid_to)

            if test_group_entries_id != last_entry_id:
                last_entry_id = test_group_entries_id
                if current_entry:
                    d = current_entry._asdict()
                    d.pop('args')
                    entries += [ group_entry_t(args=args, **d) ]
                if tests_id:
                    file_key = self.get_file_key_from_id(c, tests_file_id)
                    test = test_t(tests_id, test_filename, file_key, tests_file_id, tests_valid_from, tests_valid_to)

                    current_entry = group_entry_t(test_group_entries_id, test_group_entries_name, test_group_entries_order_position, test_group_entries_valid_from, test_group_entries_valid_to, [], test)
                args = []

            if values_id:
                args += [ arg ]

            if test_groups_id != last_group_id:
                last_group_id = test_groups_id
                if current_group:
                    d = current_group._asdict()
                    d.pop('entries')
                    group = test_group_t(entries=entries, **d)
                    groups_id_map[group.id] = group
                    groups_name_map.setdefault(group.name, [])
                    groups_name_map[group.name] += [ group ]
                if test_groups_id:
                    current_group = test_group_t(test_groups_id, test_groups_name, test_groups_description, test_groups_notes, test_groups_valid_from, test_groups_valid_to, [])
                    entries = []

        return groups_id_map, groups_name_map

    def make_key_dict(self, obj, unwanted_attrs=['id', 'valid_from', 'valid_to', 'file_id']):
        d = copy.copy(obj) if isinstance(obj, dict) else dict(obj._asdict())
        for unwanted in unwanted_attrs:
            d.pop(unwanted, None)
        for key in d:
            attr = d[key]
            if isinstance(attr, list):
                new_attr = []
                for child in attr:
                    new_attr += [ self.make_key_dict(child, unwanted_attrs) ]
                d[key] = new_attr
            elif key == "test":
                new_attr += [ self.make_key_dict(attr, unwanted_attrs) ]
                d[key] = new_attr
            else:
                d[key] = attr
        return d

    def make_key(self, obj):
        return str(self.make_key_dict(obj))

    def commit(self, c):
        self.dbrefs[c].commit()
